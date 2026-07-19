"""Alexandre - sandbox parent: config, MCP tool wrappers, watches the cell."""

import json
import os
import select
import subprocess
import sys
import time

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

        # Ensure allowed directories exist (best-effort; /testbed lives in Docker)
        for directory in config.allowed_directories:
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Public interface (contract.protocols.Sandbox)
    # ------------------------------------------------------------------

    def run(self, code: str) -> str:
        """Execute the LLM's code inside the isolated cell process."""
        if not code.strip():
            return feedback.NO_CODE

        # --- IPC pipes (only used when an MCP client is connected) ---
        # req pipe: cell writes tool-call requests → supervisor reads
        # res pipe: supervisor writes results → cell reads
        use_mcp = bool(self._mcp_client and self._mcp_tools)
        if use_mcp:
            req_r, req_w = os.pipe()  # cell writes to req_w; supervisor reads from req_r
            res_r, res_w = os.pipe()  # supervisor writes to res_w; cell reads from res_r
        else:
            req_r = req_w = res_r = res_w = None

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
            [sys.executable, self._cell_script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            preexec_fn=_set_limits,
            pass_fds=pass_fds,
            text=False,  # binary mode; we decode ourselves
        )

        # Close the child-side FDs in the parent process after fork
        if use_mcp:
            os.close(req_w)
            os.close(res_r)

        # Send the code payload to the cell via stdin, then close it
        proc.stdin.write(code.encode())
        proc.stdin.close()

        stdout_chunks: list[bytes] = []
        deadline = time.monotonic() + self.config.max_execution_time_seconds
        timed_out = False

        # --- Event loop ---
        # Monitor the cell's stdout and (if MCP is active) the request pipe.
        watch_fds = [proc.stdout]
        if use_mcp:
            req_r_file = os.fdopen(req_r, "rb", buffering=0)
            res_w_file = os.fdopen(res_w, "wb", buffering=0)
            watch_fds.append(req_r_file)
            req_buf = b""
        else:
            req_r_file = res_w_file = None
            req_buf = b""

        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break

                try:
                    readable, _, _ = select.select(watch_fds, [], [], min(remaining, 0.1))
                except ValueError:
                    # An fd was closed (process exited)
                    break

                for fd in readable:
                    if fd is proc.stdout:
                        chunk = proc.stdout.read1(4096)  # type: ignore[attr-defined]
                        if chunk:
                            stdout_chunks.append(chunk)
                        else:
                            # stdout closed → cell has exited
                            watch_fds.remove(proc.stdout)
                    elif use_mcp and fd is req_r_file:
                        chunk = req_r_file.read1(4096)  # type: ignore[attr-defined]
                        req_buf += chunk
                        # Messages are newline-delimited JSON
                        while _MSG_SEP in req_buf:
                            line, req_buf = req_buf.split(_MSG_SEP, 1)
                            self._dispatch_tool_call(line.strip(), res_w_file)

                # If cell stdout has been closed and we've drained it, we're done
                if proc.stdout not in watch_fds:
                    break

                # Also exit if the process has already finished
                if proc.poll() is not None and not readable:
                    # Drain any remaining output
                    rest = proc.stdout.read()
                    if rest:
                        stdout_chunks.append(rest)
                    break

        finally:
            if use_mcp:
                try:
                    req_r_file.close()
                except Exception:
                    pass
                try:
                    res_w_file.close()
                except Exception:
                    pass

        # --- Terminate / wait ---
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

        # Memory limit hit → SIGKILL (-9) or MemoryError in output
        if exit_code in (-9, -11) or "MemoryError" in stdout:
            mem_msg = feedback.MEMORY.format(mb=self.config.max_memory_mb)
            return f"{stdout}\n{mem_msg}" if stdout else mem_msg

        # final_answer() prints the FINAL_PREFIX; the loop detects it
        if feedback.FINAL_PREFIX in stdout:
            idx = stdout.rfind(feedback.FINAL_PREFIX)
            return stdout[idx:]

        return stdout

    # ------------------------------------------------------------------
    # MCP dispatch (called from the event loop when a tool request arrives)
    # ------------------------------------------------------------------

    def _dispatch_tool_call(self, raw: bytes, res_w: "IO[bytes]") -> None:
        """Parse a tool-call request from the cell and write the result back."""
        try:
            req = json.loads(raw)
            name = req["name"]
            arguments = req.get("arguments", {})
            result = self._mcp_client.call_tool(name, arguments)
            response = json.dumps({"ok": True, "result": result})
        except Exception as exc:
            response = json.dumps({"ok": False, "error": str(exc)})

        res_w.write(response.encode() + _MSG_SEP)
        res_w.flush()
