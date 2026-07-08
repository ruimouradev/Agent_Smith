"""
The interfaces between agent/ and sandbox/.
"""

from typing import Protocol


class Sandbox(Protocol):
    """What the agent needs from the sandbox."""

    manual: str  # tool documentation, generated from the MCP schemas

    def run(self, code: str) -> str:
        """
        Execute code and return the observation.

        Args:
            code: Python source to execute inside the sandbox.

        Returns:
            The observation for the LLM: the output on success, or a
            feedback message (see contract/feedback.py) on failure.
        """
        ...


class DockerBridge(Protocol):
    """What the SWE-bench tools need from the task container."""

    def exec(self, command: str) -> tuple[int, str]:
        """
        Run a shell command inside the task container.

        Args:
            command: Shell command to execute.

        Returns:
            A tuple of (exit_code, output), output being the combined
            stdout and stderr of the command.
        """
        ...
