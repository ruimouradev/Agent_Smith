"""
Benchmark runner: every task x every model, through the real entry
points.

Each run is a separate `python -m agent_<benchmark>` process (the
real entry point), so the wall-clock times include process startup,
exactly as they will be measured. Results land in
benchmarks/results/<model>/<task>.json (the solution.json evidence
the report is built on) and a summary table is printed at the end.

The table reports success as claimed by the agent; correctness is
validated separately with the moulinette.

Usage:
    uv run python benchmarks/run.py --benchmark mbpp \\
        --tasks-dir benchmarks/tasks --models "model/a,model/b"
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_one(benchmark: str, task_file: Path, output: Path,
            model: str | None) -> float:
    """Run one task in a fresh process; return its wall-clock seconds.

    Args:
        benchmark: name of the benchmark (selects the entry point).
        task_file: The task.json to solve.
        output: Where the solution.json must be written.
        model: Model override, or None for the configured default.

    Returns:
        Wall-clock duration of the whole process, in seconds.
    """
    command = [sys.executable, "-m", f"agent_{benchmark}",
               "--task-file", str(task_file), "--output", str(output)]
    if model:
        command += ["--model-name", model]
    start = time.monotonic()
    subprocess.run(command, cwd=ROOT, capture_output=True)
    return time.monotonic() - start


def summarize(results: list[dict]) -> None:
    """Print one aggregate line per model."""
    models = sorted({r["model"] for r in results})
    print(f"\n{'model':<40} {'ok':>5} {'iters':>6} {'in':>8} "
          f"{'out':>7} {'wall s':>7}")
    for model in models:
        rows = [r for r in results if r["model"] == model]
        n = len(rows)
        ok = sum(1 for r in rows if r["success"])
        print(f"{model:<40} {ok:>3}/{n} "
              f"{sum(r['iterations'] for r in rows) / n:>6.1f} "
              f"{sum(r['input_tokens'] for r in rows) / n:>8.0f} "
              f"{sum(r['output_tokens'] for r in rows) / n:>7.0f} "
              f"{sum(r['wall_seconds'] for r in rows) / n:>7.1f}")


def main() -> None:
    """Run the full grid and write the evidence files."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", required=True,
                        choices=["mbpp", "swebench"])
    parser.add_argument("--tasks-dir", required=True,
                        help="directory with the task .json files")
    parser.add_argument("--models", default="",
                        help="comma-separated model names; empty runs "
                             "the configured default once")
    parser.add_argument("--results-dir",
                        default=str(ROOT / "benchmarks" / "results"))
    args = parser.parse_args()

    task_files = sorted(Path(args.tasks_dir).glob("*.json"))
    if not task_files:
        sys.exit(f"no task .json files in {args.tasks_dir}")
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    results = []

    for model in models or [None]:
        label = model or "default"
        # model names contain "/" and ":", not valid in paths
        out_dir = Path(args.results_dir) / label.replace("/", "_")\
            .replace(":", "_")
        out_dir.mkdir(parents=True, exist_ok=True)
        for task_file in task_files:
            output = out_dir / f"{task_file.stem}.solution.json"
            wall = run_one(args.benchmark, task_file, output, model)
            solution = json.loads(output.read_text())
            results.append({
                "model": label,
                "task": task_file.stem,
                "success": solution["success"],
                "iterations": solution["iterations"],
                "input_tokens": solution["total_input_tokens"],
                "output_tokens": solution["total_output_tokens"],
                "wall_seconds": wall,
            })
            r = results[-1]
            state = "ok  " if r["success"] else "fail"
            print(f"{label:<40} {r['task']:<24} {state} "
                  f"iters={r['iterations']} wall={wall:.1f}s",
                  flush=True)

    summarize(results)


if __name__ == "__main__":
    main()
