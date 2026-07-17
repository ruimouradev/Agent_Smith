"""
Entry point for MBPP: python -m agent_mbpp --task-file --output.

Builds the real pieces (task, profile, provider, budget, sandbox) and
hands them to the loop. A failure before the loop starts still writes
a valid solution.json with the error field set, and the process exits
with 0 either way.
"""

import argparse
import json
from pathlib import Path

from agent.budget import Budget
from agent.loop import run
from agent.profiles import mbpp_profile
from agent.providers import from_config
from contract import MBPPTaskInput, SolutionOutput
from contract.protocols import Sandbox

# anchored to this file, so the entry point works from any cwd
_MODELS_JSON = Path(__file__).parent / "configs" / "models.json"


def main() -> None:
    """Parse the CLI arguments and drive one MBPP task."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-name", default=None)
    parser.add_argument("--provider-url", default=None)
    args = parser.parse_args()

    task_id = "unknown"
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
        run(profile, _make_sandbox(), provider, budget, args.output)
    except Exception as exc:  # before the loop: still write a solution
        _write_failure(args.output, task_id, f"{type(exc).__name__}: {exc}")


def _make_sandbox() -> Sandbox:
    """Build the sandbox wired to the MBPP tools."""
    # WAITING FOR sandbox/supervisor.py DONE
    raise NotImplementedError("sandbox/supervisor.py not ready yet")


def _write_failure(output: str, task_id: str, error: str) -> None:
    """Write a valid solution.json for a run that never started."""
    result = SolutionOutput.from_steps(
        task_id=task_id, benchmark="mbpp", success=False,
        solution="", steps=[], total_time_seconds=0.0, error=error,
    )
    Path(output).write_text(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
