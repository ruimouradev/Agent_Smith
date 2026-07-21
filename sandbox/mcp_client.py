"""Alexandre - MCP client: connects to tool server, discovers tools, proxies calls."""

import asyncio
import json
import shlex
import threading
from contextlib import AsyncExitStack
from typing import Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client


class MCPClient:
    """
    Synchronous wrapper around an async MCP ClientSession.

    Runs the async event loop in a background daemon thread so the supervisor
    can call list_tools() and call_tool() synchronously without blocking the
    main process — and without restarting a new event loop for each call.

    Usage:
        with MCPClient() as client:
            client.connect_stdio("python mcp_tools_mbpp.py")
            tools = client.list_tools()
            result = client.call_tool("run_tests", {"code": "..."})
    """

    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._session: Optional[ClientSession] = None
        self._exit_stack: Optional[AsyncExitStack] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(self, coro):
        """Submit a coroutine to the background loop and block for the result."""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()  # blocks until done; propagates exceptions

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect_stdio(self, command: str) -> None:
        """Connect to an MCP server that is launched as a subprocess (stdio)."""
        parts = shlex.split(command)
        self._run(self._connect_stdio(parts[0], parts[1:]))

    def connect_http(self, url: str) -> None:
        """Connect to an MCP server running over streamable HTTP."""
        self._run(self._connect_http(url))

    async def _connect_stdio(self, executable: str, args: list) -> None:
        params = StdioServerParameters(command=executable, args=args)
        self._exit_stack = AsyncExitStack()
        read, write = await self._exit_stack.enter_async_context(stdio_client(params))
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(read, write)
        )
        await self._session.initialize()

    async def _connect_http(self, url: str) -> None:
        self._exit_stack = AsyncExitStack()
        read, write, _ = await self._exit_stack.enter_async_context(
            streamablehttp_client(url)
        )
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(read, write)
        )
        await self._session.initialize()

    # ------------------------------------------------------------------
    # Protocol operations
    # ------------------------------------------------------------------

    def list_tools(self) -> list[dict]:
        """
        Fetch tool schemas from the connected MCP server.

        Returns a list of dicts with keys: name, description, inputSchema.
        """
        result = self._run(self._session.list_tools())
        return [
            {
                "name": tool.name,
                "description": tool.description or "",
                "inputSchema": tool.inputSchema,
            }
            for tool in result.tools
        ]

    def call_tool(self, name: str, arguments: dict) -> str:
        """
        Call a tool by name with keyword arguments.

        Returns the tool's result as a single string (text blocks joined by newline).
        """
        result = self._run(self._session.call_tool(name, arguments))
        parts = []
        for block in result.content:
            if hasattr(block, "text"):
                parts.append(block.text)
            else:
                parts.append(str(block))
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Tear down the session and stop the background event loop."""
        if self._exit_stack is not None:
            try:
                self._run(self._exit_stack.aclose())
            except Exception:
                pass  # best effort cleanup
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


# ------------------------------------------------------------------
# Module-level helpers used by cli.py
# ------------------------------------------------------------------

def factory(
    mcp_stdio: Optional[str] = None,
    mcp_server: Optional[str] = None,
) -> Optional[MCPClient]:
    """
    Build and connect an MCPClient from CLI flags.

    Returns None if neither flag is provided (no MCP server requested).
    """
    if not mcp_stdio and not mcp_server:
        return None
    client = MCPClient()
    if mcp_stdio:
        client.connect_stdio(mcp_stdio)
    else:
        client.connect_http(mcp_server)
    return client


def generate_manual(tools: list[dict]) -> str:
    """
    Convert tool schemas into a human-readable text document.

    The result is stored as `sandbox_manual` in the cell's execution namespace
    so the LLM's system prompt can include accurate tool documentation.
    """
    if not tools:
        return ""

    lines = ["Available tools:", ""]
    for tool in tools:
        name = tool["name"]
        desc = (tool.get("description") or "").strip()
        schema = tool.get("inputSchema") or {}
        props = schema.get("properties") or {}
        required = schema.get("required") or []

        lines.append(f"  {name}({_params_str(props, required)})")
        if desc:
            lines.append(f"    {desc}")
        # the signature line above already carries the names and types,
        # so only a parameter with its own description adds anything
        for param, spec in props.items():
            pdesc = spec.get("description", "")
            if pdesc:
                lines.append(f"    - {param}: {pdesc}")
        lines.append("")

    return "\n".join(lines)


def _params_str(props: dict, required: list) -> str:
    """Format tool parameters as a Python-style signature string."""
    parts = []
    for param, spec in props.items():
        ptype = spec.get("type", "any")
        suffix = "" if param in required else "=None"
        parts.append(f"{param}: {ptype}{suffix}")
    return ", ".join(parts)
