"""Alexandre - sandbox entrypoint: CLI parser."""

import sys
import argparse
from pathlib import Path

from contract.models import SandboxConfig
from sandbox import mcp_client as mcp
from sandbox.supervisor import LocalSandbox


def main():
    parser = argparse.ArgumentParser(description="Agent Smith Sandbox CLI")
    parser.add_argument("config", nargs="?", default=None, help="Path to config.json")
    parser.add_argument("--mcp-stdio", type=str, default=None,
                        help="Launch an MCP server as a subprocess (stdio transport)")
    parser.add_argument("--mcp-server", type=str, default=None,
                        help="Connect to a running MCP server (streamable-HTTP URL)")

    args = parser.parse_args()

    client = None
    try:
        # --- Config ---
        if args.config:
            config_path = Path(args.config)
            if not config_path.exists():
                print(f"Error: config file not found: {args.config}", file=sys.stderr)
                sys.exit(1)
            config = SandboxConfig.model_validate_json(config_path.read_text())
        else:
            config = SandboxConfig()

        # --- MCP client (Phase 2) ---
        # factory() returns None when neither flag is provided, so plain sandbox
        # runs (tests 1-5) are completely unaffected.
        client = mcp.factory(args.mcp_stdio, args.mcp_server)

        tools: list[dict] = []
        manual: str = ""
        if client is not None:
            tools = client.list_tools()
            manual = mcp.generate_manual(tools)

        # --- Sandbox ---
        sandbox = LocalSandbox(
            config=config,
            manual=manual,
            mcp_client=client,
            mcp_tools=tools,
        )

        # Read the code payload from stdin, then run
        code = sys.stdin.read()
        observation = sandbox.run(code)

        # Print cleanly to stdout — no sandbox logging mixed in
        print(observation, end="")

    except Exception as exc:
        # Graceful fallback: never crash silently during evaluation
        print(f"Sandbox error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        # Close the MCP session regardless of success or failure
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
