"""
Render the benchmark figures from the solution.json evidence.

Reads the same files as benchmarks/report.py (one folder per model) and
writes PNG charts: pass rate per model, mean iterations per model, and an
efficiency scatter (iterations vs output tokens). Run with:

    uv run --with matplotlib python plots.py \
        --results-dir benchmarks/results --out-dir benchmarks/figures
"""

import argparse
import json
from pathlib import Path

import matplotlib  # type: ignore
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # type: ignore  # noqa: E402


def load(results_dir):
    """One record per run: model label and the parsed solution."""
    runs = []
    for path in sorted(Path(results_dir).glob("*/*.json")):
        runs.append({"model": path.parent.name,
                     "solution": json.loads(path.read_text())})
    return runs


def per_model(runs):
    """Aggregate the runs into one row of metrics per model."""
    models = sorted({r["model"] for r in runs})
    rows = []
    for m in models:
        sols = [r["solution"] for r in runs if r["model"] == m]
        answered = [s for s in sols if s["steps"]]
        n = len(sols)
        passed = sum(1 for s in sols if s["success"])
        # iterations/tokens only from runs that actually answered, so a
        # rate-limited 0/0 does not drag the averages to zero
        it = [s["iterations"] for s in answered] or [0]
        out = [s["total_output_tokens"] for s in answered] or [0]
        rows.append({
            "model": m, "n": n, "passed": passed,
            "pass_rate": passed / n if n else 0,
            "mean_iters": sum(it) / len(it),
            "mean_out": sum(out) / len(out),
            "answered": len(answered),
        })
    return rows


def _short(name):
    """Trim a provider-prefixed model id to something axis-friendly."""
    return name.replace("_free", "").split("_")[-1][:24]


def bar(rows, key, title, xlabel, out_path, better_low=False):
    """One horizontal bar chart, sorted by the plotted metric."""
    rows = sorted(rows, key=lambda r: r[key], reverse=not better_low)
    labels = [_short(r["model"]) for r in rows]
    values = [r[key] for r in rows]
    plt.figure(figsize=(9, max(3, 0.5 * len(rows))))
    plt.barh(labels, values, color="#4C72B0")
    plt.xlabel(xlabel)
    plt.title(title)
    plt.gca().invert_yaxis()
    plt.tight_layout()
    plt.savefig(out_path, dpi=120)
    plt.close()


def scatter(rows, out_path):
    """Effort vs cost: mean iterations against mean output tokens."""
    plt.figure(figsize=(8, 6))
    for r in rows:
        if not r["answered"]:
            continue
        plt.scatter(r["mean_iters"], r["mean_out"], s=60, color="#55A868")
        plt.annotate(_short(r["model"]),
                     (r["mean_iters"], r["mean_out"]),
                     fontsize=8, xytext=(4, 4), textcoords="offset points")
    plt.xlabel("Mean iterations")
    plt.ylabel("Mean output tokens")
    plt.title("Efficiency: iterations vs output tokens")
    plt.tight_layout()
    plt.savefig(out_path, dpi=120)
    plt.close()


def main():
    """Write every figure for the results directory given."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    runs = load(args.results_dir)
    if not runs:
        raise SystemExit(f"no solution files under {args.results_dir}")
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = per_model(runs)

    bar(rows, "pass_rate", "Pass rate by model", "pass rate",
        out / "pass_rate.png")
    bar(rows, "mean_iters", "Mean iterations by model (answered runs)",
        "mean iterations", out / "mean_iters.png", better_low=True)
    scatter(rows, out / "efficiency.png")
    print(f"wrote 3 figures to {out}")


if __name__ == "__main__":
    main()
