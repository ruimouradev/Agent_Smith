import argparse
import json
from pathlib import Path

from agent.budget import Budget
from agent.loop import run
from agent.profiles import swebench_profile
from agent.providers import from_config
from contract import SolutionOutput, SWEBenchTaskInput
from contract.protocols import Sandbox


def main() -> None:
    """Parse the evaluation arguments and drive one SWE-bench task."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model-name", default=None)
    parser.add_argument("--provider-url", default=None)
    args = parser.parse_args()

    task_id = "unknown"
    try:
        raw = json.loads(Path(args.task_file).read_text())
        task_id = str(raw.get("instance_id", task_id))
        task = SWEBenchTaskInput(**raw)
        profile = swebench_profile(task)
        provider = from_config("configs/models.json",
                               model=args.model_name,
                               base_url=args.provider_url)
        budget = Budget(profile.max_iterations, profile.max_input_tokens,
                        profile.max_output_tokens, profile.max_seconds)
        run(profile, _make_sandbox(), provider, budget, args.output)
    except Exception as exc:
        _write_failure(args.output, task_id, f"{type(exc).__name__}: {exc}")


def _make_sandbox() -> Sandbox:
    """Build the sandbox wired to the SWE-bench tools and the bridge."""

    raise NotImplementedError("sandbox/supervisor.py not ready yet")


def _write_failure(output: str, task_id: str, error: str) -> None:
    """Write a valid solution.json for a run that never started."""
    result = SolutionOutput.from_steps(
        task_id=task_id, benchmark="swebench", success=False,
        solution="", steps=[], total_time_seconds=0.0, error=error,
    )
    Path(output).write_text(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
