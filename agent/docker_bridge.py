import atexit
import subprocess
import tempfile
import time
from pathlib import Path


class DockerBridge:
    def __init__(self, image: str, ttl_seconds: int = 870,
                 exec_timeout: int = 300, workdir: str = "/testbed"):
        self.image = image
        self.ttl_seconds = ttl_seconds
        self.exec_timeout = exec_timeout
        self.workdir = workdir
        self.cid = ""
        self._proc: subprocess.Popen | None = None

    def start(self, timeout: int = 600) -> None:

        cidfile = Path(tempfile.mkstemp(suffix=".cid")[1])
        cidfile.unlink()
        self._proc = subprocess.Popen(
            ["docker", "run", "--rm", "-i", "--cidfile", str(cidfile),
             self.image, "timeout", str(self.ttl_seconds), "cat"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        atexit.register()

    def exec(self, command: str) -> tuple[int, str]:

        start = time.monotonic()
        try:
            result = subprocess.run(
                ["docker", "exec", "-w", self.workdir, self.cid,
                 "timeout", str(self.exec_timeout), "bash", "-lc", command],
                capture_output=True, text=True,
                timeout=self.exec_timeout + 10,
            )
        except subprocess.TimeoutExpired:
            return 124, f"command timed out after {self.exec_timeout}s"
        output = result.stdout + result.stderr
        if time.monotonic() - start >= self.exec_timeout:
            output += f"\ncommand timed out after {self.exec_timeout}s"
        return result.returncode, output