"""The entry points, run as real subprocesses: one task each,
checked for exit 0 and a valid solution.json no matter what goes
wrong."""

import json
import os
import subprocess
import sys
from pathlib import Path

from contract import BENCHMARK_MBPP, BENCHMARK_SWEBENCH

# the entry points are named after the benchmark they serve
MBPP_SHIM = f"agent_{BENCHMARK_MBPP}"
SWE_SHIM = f"agent_{BENCHMARK_SWEBENCH}"

ROOT = Path(__file__).resolve().parents[1]


def run_shim(module: str, task_file: Path, output: Path,
             env: dict) -> subprocess.CompletedProcess:
    """Invoke one entry point as a separate process."""
    return subprocess.run(
        [sys.executable, "-m", module,
         "--task-file", str(task_file), "--output", str(output)],
        cwd=ROOT, env=env, capture_output=True, text=True)


def clean_env(**extra: str) -> dict:
    """The current environment stripped of provider keys."""
    keys = ("MISTRAL_API_KEY", "GROQ_API_KEY",
            "GEMINI_API_KEY", "OPENROUTER_API_KEY")
    env = {key: value for key, value in os.environ.items()
           if key not in keys}
    env.update(extra)
    return env


def test_mbpp_without_keys_exits_zero_with_clear_error(tmp_path):
    """No API keys: still exit 0, still a valid solution.json, and
    the error names the missing variables."""
    task = tmp_path / "task.json"
    task.write_text(json.dumps({
        "task_id": 9, "task_definition": "d",
        "function_definition": "def f():",
        "test_imports": [], "test_list": [],
    }))
    output = tmp_path / "solution.json"
    result = run_shim(MBPP_SHIM, task, output, clean_env())
    written = json.loads(output.read_text())
    assert result.returncode == 0
    assert "no API keys" in written["error"]
    # the id was salvaged from the raw JSON before anything could fail
    assert written["task_id"] == "9"


def test_mbpp_with_broken_task_file_exits_zero(tmp_path):
    """Unparseable task file: exit 0, valid output, id 'unknown'."""
    task = tmp_path / "bad.json"
    task.write_text("{broken")
    output = tmp_path / "solution.json"
    result = run_shim(MBPP_SHIM, task, output, clean_env())
    written = json.loads(output.read_text())
    assert result.returncode == 0
    assert written["task_id"] == "unknown"


def test_swe_writes_a_failure_when_the_container_cannot_start(tmp_path):
    """With a key set the run reaches the docker bridge. A bogus
    image cannot start, and the failure still lands in a valid
    solution.json with exit code 0, as a graded run requires."""
    task = tmp_path / "task.json"
    task.write_text(json.dumps({
        "instance_id": "i-1", "problem_statement": "b",
        "docker_image": "localhost/no-such-image:none", "eval_script": "s",
    }))
    output = tmp_path / "solution.json"
    result = run_shim(SWE_SHIM, task, output,
                      clean_env(OPENROUTER_API_KEY="fake"))
    written = json.loads(output.read_text())
    assert result.returncode == 0
    assert written["success"] is False
    assert written["error"]
