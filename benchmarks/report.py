"""
Turn the solution.json evidence into the tables of the report.

Reads the files benchmarks/run.py wrote and prints markdown: one row
per model x task, one row per model for provider reliability, and the
intermediary metrics that can be derived from the step trace. Nothing
is recomputed from the models, so the tables and the evidence on disk
can never disagree.

Usage:
    uv run python benchmarks/report.py --results-dir benchmarks/results
"""

import argparse
import json
import re
from pathlib import Path


def load(results_dir: Path) -> list[dict]:
    """
    Read every solution.json under the results directory.

    Args:
        results_dir: The directory run.py wrote, one folder per model.

    Returns:
        One dict per run, with the model label and the parsed solution.
    """
    runs = []
    for path in sorted(results_dir.glob("*/*.json")):
        runs.append({
            "model": path.parent.name,
            "task": path.stem.replace(".solution", ""),
            "solution": json.loads(path.read_text()),
        })
    return runs


def results_table(runs: list[dict]) -> str:
    """Build the model x task grid the report asks for."""
    lines = ["| Model | Task | Pass | Iter | In | Out | Time (s) |",
             "|---|---|---|---|---|---|---|"]
    for run in runs:
        s = run["solution"]
        lines.append(
            f"| `{run['model']}` | {run['task']} "
            f"| {'yes' if s['success'] else 'no'} | {s['iterations']} "
            f"| {s['total_input_tokens']} | {s['total_output_tokens']} "
            f"| {s['total_time_seconds']:.1f} |")
    return "\n".join(lines)


def reliability_table(runs: list[dict]) -> str:
    """
    Build the provider reliability table from the step trace.

    A run that produced no step at all never got an answer, so the
    share of runs that did is the availability seen from here.
    """
    lines = ["| Model | Provider | Requests | Retries | "
             "Avg response (s) | Runs answered |",
             "|---|---|---|---|---|---|"]
    for model in sorted({r["model"] for r in runs}):
        rows = [r["solution"] for r in runs if r["model"] == model]
        steps = [s for r in rows for s in r["steps"]]
        if not steps:
            lines.append(f"| `{model}` | — | 0 | 0 | — | 0/{len(rows)} |")
            continue
        requests = sum(1 + s["retries"] for s in steps)
        retries = sum(s["retries"] for s in steps)
        avg = sum(s["request_time_ms"] for s in steps) / len(steps) / 1000
        answered = sum(1 for r in rows if r["steps"])
        url = steps[0]["api_url"]
        lines.append(
            f"| `{model}` | {url} | {requests} | {retries} "
            f"| {avg:.2f} | {answered}/{len(rows)} |")
    return "\n".join(lines)


def _patched_files(solution: str) -> set[str]:
    """Names of the files a unified diff touches."""
    return {Path(m).name
            for m in re.findall(r"^\+\+\+ b/(\S+)", solution, re.M)}


def first_touch(run: dict) -> int | None:
    """
    The step that first names a file appearing in the final patch.

    Exploration efficiency: a low number means the agent found the
    right file early instead of wandering the repository.

    Args:
        run: One entry from load().

    Returns:
        The 1-based step, or None when the run has no patch.
    """
    files = _patched_files(run["solution"].get("solution", ""))
    if not files:
        return None
    for step in run["solution"]["steps"]:
        if any(name in step["sandbox_input"] for name in files):
            return step["step"]
    return None


def test_gap(run: dict) -> float | None:
    """
    Mean number of steps between one test run and the next.

    Iteration discipline: a large gap means long stretches of editing
    without checking whether anything improved.
    """
    tested = [s["step"] for s in run["solution"]["steps"]
              if "run_tests" in s["sandbox_input"]]
    if len(tested) < 2:
        return None
    gaps = [b - a for a, b in zip(tested, tested[1:])]
    return sum(gaps) / len(gaps)


def intermediary_table(runs: list[dict]) -> str:
    """Build the table of intermediary metrics."""
    lines = ["| Model | Task | First touch of a patched file | "
             "Mean steps between tests |",
             "|---|---|---|---|"]
    for run in runs:
        touch = first_touch(run)
        gap = test_gap(run)
        lines.append(
            f"| `{run['model']}` | {run['task']} "
            f"| {touch if touch else '—'} "
            f"| {f'{gap:.1f}' if gap else '—'} |")
    return "\n".join(lines)


def main() -> None:
    """Print every table for the results directory given."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", required=True)
    args = parser.parse_args()

    runs = load(Path(args.results_dir))
    if not runs:
        raise SystemExit(f"no solution files under {args.results_dir}")

    print("### Results\n")
    print(results_table(runs))
    print("\n### Provider reliability\n")
    print(reliability_table(runs))
    print("\n### Intermediary metrics\n")
    print(intermediary_table(runs))


if __name__ == "__main__":
    main()
