"""Sandbox parent: spawn the cell, enforce limits, proxy MCP tool calls.

Runs the agent's code in a separate cell.py process, feeds it the code
on stdin, watches its output under a wall-clock and memory limit, and
(when an MCP client is connected) services the tool-call requests the
cell sends over a pair of pipes.
"""

import json
import os
import select
import subprocess
import sys
import time
import io
from typing import IO, cast

from contract import feedback
from contract.models import SandboxConfig

# Separator used between JSON messages on the IPC pipe
_MSG_SEP = b"\n"


class LocalSandbox:
    """The local Sandbox implementation that executes code via cell.py.

    Conforms to contract.protocols.Sandbox so the agent loop can use it
    without knowing anything about subprocess mechanics.
    """

    def __init__(self, config: SandboxConfig, manual: str = "",
                 mcp_client=None, mcp_tools: list | None = None):
        self.config = config
        self.manual = manual
        self._mcp_client = mcp_client
        self._mcp_tools: list[dict] = mcp_tools or []
        self._cell_script = os.path.join(os.path.dirname(__file__), "cell.py")

        # Ensure allowed directories exist. Best effort: /testbed lives
        # in the Docker container, not on the local filesystem.
        for directory in config.allowed_directories:
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError:
                pass

    def run(self, code: str) -> str:
        """Execute the LLM's code inside the isolated cell process."""
        if not code.strip():
            return feedback.NO_CODE

        # IPC pipes, only used when an MCP client is connected. The cell
        # writes tool-call requests to req and reads results from res.
        # -1 stands for "no pipe" and is never touched unless use_mcp.
        use_mcp = bool(self._mcp_client and self._mcp_tools)
        req_r = req_w = res_r = res_w = -1
        if use_mcp:
            req_r, req_w = os.pipe()
            res_r, res_w = os.pipe()

        env = os.environ.copy()
        env["SANDBOX_CONFIG_JSON"] = self.config.model_dump_json()
        env["SANDBOX_MANUAL"] = self.manual
        if use_mcp:
            env["MCP_TOOLS_JSON"] = json.dumps(self._mcp_tools)
            env["MCP_REQ_FD"] = str(req_w)
            env["MCP_RES_FD"] = str(res_r)

        pass_fds = (req_w, res_r) if use_mcp else ()

        def _set_limits():
            import resource
            if self.config.max_memory_mb > 0:
                mem_bytes = self.config.max_memory_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))

        proc = subprocess.Popen(
            [sys.executable, "-u", self._cell_script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            preexec_fn=_set_limits,
            pass_fds=pass_fds,
            text=False,  # binary mode; we decode ourselves
        )
        # stdin/stdout are pipes we asked for, so they are never None.
        assert proc.stdin is not None and proc.stdout is not None
        stdin_pipe = proc.stdin
        stdout_pipe = proc.stdout

        # Close the child-side FDs in the parent process after fork
        if use_mcp:
            os.close(req_w)
            os.close(res_r)

        # Send the code payload to the cell via stdin, then close it
        stdin_pipe.write(code.encode())
        stdin_pipe.close()

        stdout_chunks: list[bytes] = []
        deadline = time.monotonic() + self.config.max_execution_time_seconds
        timed_out = False

        # Watch the cell's stdout and, with MCP active, the request pipe.
        watch_fds: list[IO[bytes]] = [stdout_pipe]
        req_r_file: IO[bytes] | None = None
        res_w_file: IO[bytes] | None = None
        req_buf = b""
        if use_mcp:
            req_r_file = os.fdopen(req_r, "rb")
            res_w_file = os.fdopen(res_w, "wb", buffering=0)
            watch_fds.append(req_r_file)

        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break

                try:
                    readable, _, _ = select.select(
                        watch_fds, [], [], min(remaining, 0.1))
                except ValueError:
                    # An fd was closed (process exited)
                    break

                for fd in readable:
                    if fd is stdout_pipe:
                        # the pipes are buffered readers, and read1
                        # takes what is ready instead of blocking
                        chunk = cast(io.BufferedReader, fd).read1(4096)
                        if chunk:
                            stdout_chunks.append(chunk)
                        else:
                            # stdout closed means the cell has exited
                            watch_fds.remove(stdout_pipe)
                    elif use_mcp and fd is req_r_file:
                        chunk = cast(io.BufferedReader, fd).read1(4096)
                        req_buf += chunk
                        # Messages are newline-delimited JSON
                        while _MSG_SEP in req_buf:
                            line, req_buf = req_buf.split(_MSG_SEP, 1)
                            self._dispatch_tool_call(line.strip(), res_w_file)

                # If cell stdout has been closed and drained, we are done
                if stdout_pipe not in watch_fds:
                    break

                # Also exit if the process has already finished
                if proc.poll() is not None and not readable:
                    # Drain any remaining output
                    rest = stdout_pipe.read()
                    if rest:
                        stdout_chunks.append(rest)
                    break

        finally:
            if req_r_file is not None:
                try:
                    req_r_file.close()
                except Exception:
                    pass
            if res_w_file is not None:
                try:
                    res_w_file.close()
                except Exception:
                    pass

        # Timed out: terminate the cell, then report what it printed.
        if timed_out:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            stdout = b"".join(stdout_chunks).decode(errors="replace")
            feedback_msg = feedback.TIMEOUT.format(
                seconds=self.config.max_execution_time_seconds
            )
            return f"{stdout}\n{feedback_msg}" if stdout else feedback_msg

        proc.wait()
        stdout = b"".join(stdout_chunks).decode(errors="replace")
        exit_code = proc.returncode

        # Memory limit hit gives SIGKILL (-9) or MemoryError in output
        if exit_code in (-9, -11) or "MemoryError" in stdout:
            mem_msg = feedback.MEMORY.format(mb=self.config.max_memory_mb)
            return f"{stdout}\n{mem_msg}" if stdout else mem_msg

        # everything the code printed stays here, including whatever
        # came before final_answer, and the loop locates the marker.
        # Code that prints nothing would come back as an empty string,
        # leaving the model without any sign of what happened.
        return stdout if stdout.strip() else feedback.NO_OUTPUT

    def _dispatch_tool_call(self, raw: bytes,
                            res_w: "IO[bytes] | None") -> None:
        """Parse a tool-call request and write the result back to the cell."""
        try:
            req = json.loads(raw)
            name = req["name"]
            arguments = req.get("arguments", {})
            result = self._mcp_client.call_tool(name, arguments)
            response = json.dumps({"ok": True, "result": result})
        except Exception as exc:
            response = json.dumps({"ok": False, "error": str(exc)})

        if res_w is not None:
            res_w.write(response.encode() + _MSG_SEP)
            res_w.flush()
