"""
Entry point for MBPP: python -m agent_mbpp --task-file --output.

Builds the real pieces (task, profile, provider, budget, sandbox) and
hands them to the loop. A failure before the loop starts still writes
a valid solution.json with the error field set, and the process exits
with 0 either way.
"""

import argparse
import json
import shlex
import sys
from pathlib import Path

from agent.budget import Budget
from agent.loop import run
from agent.profiles import mbpp_profile
from agent.providers import from_config
from contract import MBPPTaskInput, SandboxConfig, SolutionOutput
from contract.protocols import Sandbox

# anchored to this file, so the entry point works from any cwd
_MODELS_JSON = Path(__file__).parent / "configs" / "models.json"
_TOOLS_SERVER = Path(__file__).parent / "mcp_tools_mbpp.py"

# appended to the manual generated from the discovered tool schemas
_MANUAL_EXTRA = (
    "\n"
    "Usage notes:\n"
    "- run_tests returns a JSON string: read it and check that "
    "\"success\" is true.\n"
    "- Pass the task's assert lines as test_list.\n"
    "- Only printed values reach you: call tools as "
    "print(run_tests(...)).\n"
    "- final_answer(answer): ends the task; answer is the full "
    "function source code as a string."
)


def main() -> None:
    """Parse the CLI arguments and drive one MBPP task."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-name", default=None)
    parser.add_argument("--provider-url", default=None)
    args = parser.parse_args()

    task_id = "unknown"
    client = None
    try:
        raw = json.loads(Path(args.task_file).read_text())
        task_id = str(raw.get("task_id", task_id))
        task = MBPPTaskInput(**raw)
        profile = mbpp_profile(task)
        provider = from_config(_MODELS_JSON,
                               model=args.model_name,
                               base_url=args.provider_url,
                               timeout_seconds=profile.request_timeout)
        budget = Budget(profile.max_iterations, profile.max_input_tokens,
                        profile.max_output_tokens, profile.max_seconds)
        client = _connect_tools()
        run(profile, _make_sandbox(client), provider, budget, args.output)
    except Exception as exc:  # before the loop: still write a solution
        _write_failure(args.output, task_id, f"{type(exc).__name__}: {exc}")
    finally:
        if client is not None:
            client.close()


def _connect_tools():
    """Launch mcp_tools_mbpp.py over stdio and return the client."""
    from sandbox import mcp_client as mcp
    # shlex.quote keeps the command whole when the path has spaces
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(_TOOLS_SERVER))}"
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
    return LocalSandbox(SandboxConfig(), manual=manual,
                        mcp_client=client, mcp_tools=tools)


def _write_failure(output: str, task_id: str, error: str) -> None:
    """Write a valid solution.json for a run that never started."""
    result = SolutionOutput.from_steps(
        task_id=task_id, benchmark="mbpp", success=False,
        solution="", steps=[], total_time_seconds=0.0, error=error,
    )
    Path(output).write_text(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
