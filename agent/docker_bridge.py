"""
The SWE-bench task container: startup, command execution, cleanup.

The container is started with a lifeline: its main process reads our
stdin pipe, so the kernel kills it the moment this process dies, by
any means, including kill -9, when no cleanup code can run. Normal
exits also remove it explicitly (atexit + signal handlers), and a
timeout inside the container caps its life at the task limit.
"""

import atexit
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path


class DockerBridge:
    """One task container, guaranteed to die with us."""

    def __init__(self, image: str, ttl_seconds: int = 870,
                 exec_timeout: int = 300, workdir: str = "/testbed"):
        """
        Store the task settings; nothing runs until start().

        Args:
            image: Docker image of the task (repo frozen at the bug).
            ttl_seconds: Self-destruct timer inside the container.
            exec_timeout: Per-command limit for exec().
            workdir: Directory inside the container to run commands in.
        """
        self.image = image
        self.ttl_seconds = ttl_seconds
        self.exec_timeout = exec_timeout
        self.workdir = workdir
        self.cid = ""
        self._proc: subprocess.Popen | None = None

    def start(self, timeout: int = 600) -> None:
        """
        Pull the image if needed and start the container.

        Args:
            timeout: Seconds to wait for the container id (the pull
                of a cold image is the slow part).
        """
        cidfile = Path(tempfile.mkstemp(suffix=".cid")[1])
        cidfile.unlink()  # docker refuses an existing cidfile
        # the lifeline: the container's main process reads our stdin
        # pipe. When this process dies, the pipe closes, cat exits
        # and --rm removes the container. timeout caps its life.
        self._proc = subprocess.Popen(
            ["docker", "run", "--rm", "-i", "--cidfile", str(cidfile),
             self.image, "timeout", str(self.ttl_seconds), "cat"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        atexit.register(self.close)
        signal.signal(signal.SIGTERM, self._on_signal)
        signal.signal(signal.SIGINT, self._on_signal)
        self.cid = self._wait_cid(cidfile, timeout)

    def exec(self, command: str) -> tuple[int, str]:
        """
        Run a shell command inside the task container.

        Args:
            command: Shell command to execute.

        Returns:
            A tuple of (exit_code, output), output being the combined
            stdout and stderr of the command.
        """
        start = time.monotonic()
        try:
            # timeout runs INSIDE the container: it kills the real
            # process, not just the local docker exec client. The
            # outer timeout is only the backstop for a hung docker.
            # bash -lc: a login shell activates the task's conda env
            # (the image's .bashrc), so python/pytest are the task's.
            result = subprocess.run(
                ["docker", "exec", "-w", self.workdir, self.cid,
                 "timeout", str(self.exec_timeout), "bash", "-lc", command],
                capture_output=True, text=True,
                timeout=self.exec_timeout + 10,
            )
        except subprocess.TimeoutExpired:
            return 124, f"command timed out after {self.exec_timeout}s"
        output = result.stdout + result.stderr
        # timeout exits 124 (GNU) or 128+signal (busybox), so the
        # timeout is detected by elapsed time, not by exit code
        if time.monotonic() - start >= self.exec_timeout:
            output += f"\ncommand timed out after {self.exec_timeout}s"
        return result.returncode, output

    def close(self) -> None:
        """Remove the container now; safe to call more than once."""
        if self.cid:
            subprocess.run(["docker", "rm", "-f", self.cid],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            self.cid = ""
        if self._proc and self._proc.stdin:
            self._proc.stdin.close()  # cut the lifeline as well

    def _wait_cid(self, cidfile: Path, timeout: int) -> str:
        """Wait until the container is actually running, return its id.

        The cidfile appears when the container is created, but exec()
        only works once it is running, so we wait for that state.
        """
        deadline = time.monotonic() + timeout
        cid = ""
        while time.monotonic() < deadline:
            if not cid and cidfile.exists():
                cid = cidfile.read_text().strip()
            if cid and self._is_running(cid):
                return cid
            if self._proc and self._proc.poll() is not None:
                raise RuntimeError(
                    f"docker run exited early for image {self.image}")
            time.sleep(0.1)
        raise RuntimeError(f"container start timed out for {self.image}")

    @staticmethod
    def _is_running(cid: str) -> bool:
        """Ask docker whether the container reached the running state."""
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", cid],
            capture_output=True, text=True)
        return result.stdout.strip() == "true"

    def _on_signal(self, signum, frame) -> None:
        """Clean up on SIGTERM/SIGINT, then die with the same signal."""
        self.close()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
