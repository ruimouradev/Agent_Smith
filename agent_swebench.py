"""
Entry point for SWE-bench: --task-file --output.

Builds the real pieces (task, profile, provider, budget, sandbox) and
hands them to the loop. A failure before the loop starts still writes
a valid solution.json with the error field set, and the process exits
with 0 either way.
"""

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

from agent.budget import Budget
from agent.loop import run
from agent.profiles import swe_profile
from agent.providers import from_config
from contract import (BENCHMARK_SWEBENCH, SandboxConfig, SolutionOutput,
                      SWEBenchTaskInput, feedback)
from contract.protocols import Sandbox

# anchored to this file, so the entry point works from any cwd
_MODELS_JSON = Path(__file__).parent / "configs" / "models.json"
_TOOLS_SERVER = Path(__file__).parent / "mcp_tools_swebench.py"

# appended to the manual generated from the discovered tool schemas
_MANUAL_EXTRA = (
    "\n"
    "Usage notes:\n"
    "- Wrap every tool call in print(...), edits included: "
    "print(edit_file(...)) shows whether it applied.\n"
    "- Before edit_file, read_file the exact lines and copy the old "
    "text verbatim. A from-memory old string usually misses and "
    "costs the step.\n"
    "- run_tests() runs the task's own test suite and is the only "
    "verification that works here: bare python runs and package "
    "installs usually fail against this container's environment "
    "and say nothing about your fix. Once run_tests passes, submit "
    "immediately.\n"
    "- Apply your fix with edit_file, run_tests to confirm it, then "
    "submit with final_answer(get_patch()) as the only call in the "
    "block. Batching it with other calls submits before you have "
    "seen their result, and a submission that changed nothing is "
    "refused. The patch is collected from the repository for you, "
    "so do not write the diff yourself."
)

# replaces the marker when the submitted diff is empty
_EMPTY_PATCH = (
    "\nThe answer was not accepted: the repository has no changes to "
    "submit. Edit the code to fix the issue, confirm it with run_tests, "
    "then call final_answer.\n"
)


def main() -> None:
    """Parse the CLI arguments and drive one SWE-bench task."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-name", default=None)
    parser.add_argument("--provider-url", default=None)
    args = parser.parse_args()

    task_id = "unknown"
    bridge = None
    client = None
    try:
        raw = json.loads(Path(args.task_file).read_text())
        task_id = str(raw.get("instance_id", task_id))
        task = SWEBenchTaskInput(**raw)
        profile = swe_profile(task)
        provider = from_config(_MODELS_JSON,
                               model=args.model_name,
                               base_url=args.provider_url,
                               timeout_seconds=profile.request_timeout)
        budget = Budget(profile.max_iterations, profile.max_input_tokens,
                        profile.max_output_tokens, profile.max_seconds)
        bridge = _start_bridge(task)
        client = _TrimmedTests(_connect_tools(bridge, task))
        sandbox = _PatchFromContainer(_make_sandbox(client), client)
        result = run(profile, sandbox, provider, budget, args.output)
        if not result.success and not result.solution.strip():
            _salvage_patch(client, result, args.output)
    except Exception as exc:  # before the loop: still write a solution
        _write_failure(args.output, task_id, f"{type(exc).__name__}: {exc}")
    finally:
        if client is not None:
            client.close()
        if bridge is not None:
            bridge.close()


class _TrimmedTests:
    """MCP client that returns only the test output of run_tests.

    The script wraps its results in "Start Test Output" and "End Test
    Output" markers, preceded by git status and the full diff. Kept
    whole, that noise fills the observation and buries the pass/fail
    lines the model needs.
    """

    _START = ">>>>> Start Test Output"
    _END = ">>>>> End Test Output"

    def __init__(self, client):
        """Wrap a connected client."""
        self._client = client

    def call_tool(self, name: str, arguments: dict) -> str:
        """Forward the call, trimming run_tests to its test output."""
        result = self._client.call_tool(name, arguments)
        if name != "run_tests":
            return result
        start = result.find(self._START)
        end = result.find(self._END)
        if start < 0 or end < 0:
            return result
        return result[start + len(self._START):end].strip()

    def list_tools(self) -> list[dict]:
        """Expose the server's tools unchanged."""
        return self._client.list_tools()

    def close(self) -> None:
        """Close the wrapped session."""
        self._client.close()


class _PatchFromContainer:
    """Sandbox that submits the container's real diff, not the model's.

    The model tends to hand-write the patch it passes to final_answer,
    inventing line numbers that do not match the file, so the diff
    fails to apply. Replacing the submitted string with get_patch ties
    the answer to what was actually changed in the repository. A
    submission that changed nothing is turned back into an observation,
    since an empty diff cannot carry the fix.
    """

    def __init__(self, sandbox: Sandbox, client):
        """Wrap the sandbox and the client that reads the diff."""
        self._sandbox = sandbox
        self._client = client
        self.manual = sandbox.manual

    def run(self, code: str) -> str:
        """Execute the code, replacing a submitted patch with the diff."""
        observation = self._sandbox.run(code)
        mark = observation.rfind(feedback.FINAL_PREFIX)
        if mark < 0:
            return observation
        patch = self._client.call_tool("get_patch", {})
        if "diff --git" not in patch:
            # nothing was changed, so the fix is not in place, and
            # dropping the marker keeps the loop running instead of
            # accepting it
            return observation[:mark] + _EMPTY_PATCH
        head = observation[:mark + len(feedback.FINAL_PREFIX)]
        return head + patch


def _start_bridge(task: SWEBenchTaskInput):
    """Start the task container and return the running bridge."""
    from agent.docker_bridge import DockerBridge
    bridge = DockerBridge(task.docker_image)
    bridge.start()
    return bridge


def _connect_tools(bridge, task: SWEBenchTaskInput):
    """Launch mcp_tools_swebench.py over stdio and return the client.

    The server acts on the task container, whose id and paths it
    receives through the environment.
    """
    from sandbox import mcp_client as mcp
    # written inside the container, where the tools run, since a local
    # path would not resolve there
    container_path = "/tmp/run_eval.sh"
    heredoc = (f"cat > {container_path} <<'AGENT_SMITH_EOF'\n"
               f"{task.eval_script}\nAGENT_SMITH_EOF")
    bridge.exec(heredoc)
    os.environ["SWEBENCH_CONTAINER"] = bridge.cid
    os.environ["SWEBENCH_EVAL_SCRIPT"] = container_path
    # shlex.quote keeps the command whole when the path has spaces
    command = (f"{shlex.quote(sys.executable)} "
               f"{shlex.quote(str(_TOOLS_SERVER))}")
    return mcp.factory(command, None)


def _make_sandbox(client) -> Sandbox:
    """Build the sandbox around the connected MCP client.

    The tools are discovered from the server and the manual is
    generated from their schemas, as the subject requires.
    """
    from sandbox import mcp_client as mcp
    from sandbox.supervisor import LocalSandbox
    tools = client.list_tools()
    manual = mcp.generate_manual(tools) + _MANUAL_EXTRA
    # a single tool call here can take minutes, well over the 30s default
    config = SandboxConfig(max_execution_time_seconds=600)
    return LocalSandbox(config, manual=manual,
                        mcp_client=client, mcp_tools=tools)


def _salvage_patch(client, result: SolutionOutput, output: str) -> None:
    """Fill the solution with the repository's diff when the run died
    without submitting one.

    get_patch refuses to answer unless the last test run passed and
    nothing changed since, so only a state verified green can be
    salvaged this way. A run that never reached green keeps its empty
    solution.

    Args:
        client: The connected MCP client for the task's tools.
        result: The SolutionOutput the loop wrote, updated in place.
        output: Path of the solution.json to rewrite.
    """
    try:
        patch = client.call_tool("get_patch", {})
        if "diff --git" not in patch:
            return
    except Exception:
        # any failure here leaves the solution as the loop wrote it,
        # a broken salvage must never damage the recorded run
        return
    result.solution = patch
    Path(output).write_text(result.model_dump_json(indent=2))


def _write_failure(output: str, task_id: str, error: str) -> None:
    """Write a valid solution.json for a run that never started."""
    result = SolutionOutput.from_steps(
        task_id=task_id, benchmark=BENCHMARK_SWEBENCH, success=False,
        solution="", steps=[], total_time_seconds=0.0, error=error,
    )
    Path(output).write_text(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
