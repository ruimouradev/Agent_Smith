"""Alexandre - sandbox parent: config, MCP tool wrappers, watches the cell."""

import json
import os
import subprocess
import sys

from contract import feedback
from contract.models import SandboxConfig

class LocalSandbox:
    """The local Sandbox implementation that executes code via cell.py."""
    
    def __init__(self, config: SandboxConfig, manual: str = ""):
        self.config = config
        self.manual = manual
        self._cell_script = os.path.join(os.path.dirname(__file__), "cell.py")

        # the allowed workspace must exist before code tries to use it
        for directory in config.allowed_directories:
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError:
                pass  # not creatable here (e.g. /testbed outside a container)

    def run(self, code: str) -> str:
        """Execute the LLM's code inside the isolated cell process."""
        if not code.strip():
            return feedback.NO_CODE

        env = os.environ.copy()
        env["SANDBOX_CONFIG_JSON"] = self.config.model_dump_json()
        env["SANDBOX_MANUAL"] = self.manual

        def set_limits():
            # Apply memory limit using the resource module inside the child process
            import resource
            if self.config.max_memory_mb > 0:
                mem_bytes = self.config.max_memory_mb * 1024 * 1024
                # RLIMIT_AS controls the maximum area (in bytes) of address space
                # which may be taken by the process.
                resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))

        proc = subprocess.Popen(
            [sys.executable, self._cell_script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # Merge stderr into stdout for clean output sequence
            env=env,
            preexec_fn=set_limits,
            text=True
        )

        try:
            # Communicate sends code to stdin and waits for termination or timeout
            stdout, _ = proc.communicate(input=code, timeout=self.config.max_execution_time_seconds)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            # Send SIGTERM first
            proc.terminate()
            try:
                stdout, _ = proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                # Force kill if still hanging
                proc.kill()
                stdout, _ = proc.communicate()
            
            feedback_msg = feedback.TIMEOUT.format(seconds=self.config.max_execution_time_seconds)
            return f"{stdout}\n{feedback_msg}" if stdout else feedback_msg

        # Memory limit hit generally results in SIGKILL (-9) or SIGSEGV (-11),
        # or MemoryError raised in python.
        if exit_code in (-9, -11) or "MemoryError" in stdout:
            mem_msg = feedback.MEMORY.format(mb=self.config.max_memory_mb)
            return f"{stdout}\n{mem_msg}" if stdout else mem_msg

        # Final answer checking:
        # If final_answer was called, ensure the observation exactly starts with it
        # so agent/loop.py can correctly parse the success condition.
        if feedback.FINAL_PREFIX in stdout:
            idx = stdout.rfind(feedback.FINAL_PREFIX)
            return stdout[idx:]

        return stdout
