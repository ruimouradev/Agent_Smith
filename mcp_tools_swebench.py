"""MCP tool server exposing a repository to the SWE-bench agent.

The tools let the agent read and search files, edit them in place, run
shell commands and the task's test suite, and read back the resulting
patch. They serve two runtime modes, chosen by environment: a local
testbed checkout, or a Docker container during a benchmark run.
"""

import base64
import difflib
import os
import re
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("agent-smith-swebench")

# Environment selects the mode: TESTBED_PATH for a local checkout, or
# SWEBENCH_CONTAINER (+ SWEBENCH_EVAL_SCRIPT) for a container.
_CONTAINER: str = os.environ.get("SWEBENCH_CONTAINER", "")
_EVAL_SCRIPT: str = os.environ.get("SWEBENCH_EVAL_SCRIPT", "")
_TESTBED: str = os.environ.get("TESTBED_PATH", "/testbed")

# An edit invalidates the previous test run. get_patch refuses to hand
# out an untested diff or a failing one, so a submission always
# follows a passing test run.
_edited_since_test = False
_tests_failed = False


def _exec(cmd: str, workdir: str | None = None,
          stdin: str | None = None, timeout: int = 60) -> tuple[int, str]:
    """
    Run a shell command in the testbed context.

    If SWEBENCH_CONTAINER is set, execute inside the Docker container.
    Otherwise, run directly as a subprocess in the local testbed directory.
    A large payload goes through stdin, which has no length limit, unlike
    a command-line argument capped by ARG_MAX.
    """
    try:
        if _CONTAINER:
            cwd = workdir or "/testbed"
            docker_cmd = ["docker", "exec", "-w", cwd]
            if stdin is not None:
                docker_cmd.append("-i")
            docker_cmd += [_CONTAINER, "bash", "-c", cmd]
            r = subprocess.run(
                docker_cmd, input=stdin,
                capture_output=True, text=True, timeout=timeout,
            )
        else:
            cwd = workdir or _TESTBED
            r = subprocess.run(
                cmd, shell=True, cwd=cwd, input=stdin,
                capture_output=True, text=True, timeout=timeout,
            )
        return r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return 1, "Command timed out."
    except Exception as exc:
        return 1, str(exc)


def _resolve(filepath: str) -> Path:
    """
    Resolve a path against the testbed.

    A relative path is taken from the testbed root. Outside a container
    the container's /testbed prefix is remapped onto the local testbed
    directory, so a caller can use the same /testbed paths in both modes.
    """
    p = Path(filepath)
    if not p.is_absolute():
        return Path(_TESTBED) / p
    if not _CONTAINER and _TESTBED != "/testbed":
        try:
            return Path(_TESTBED) / p.relative_to("/testbed")
        except ValueError:
            pass
    return p


def _read_raw(filepath: str) -> tuple[bool, str]:
    """Read raw file content. Returns (ok, content_or_error)."""
    if _CONTAINER:
        code, out = _exec(f"cat {filepath}")
        return code == 0, out
    path = _resolve(filepath)
    try:
        return True, path.read_text(errors="replace")
    except Exception as exc:
        return False, str(exc)


def _write_raw(filepath: str, content: str) -> tuple[bool, str]:
    """Write raw file content. Returns (ok, error_or_empty)."""
    if _CONTAINER:
        encoded = base64.b64encode(content.encode()).decode()
        # the payload travels on stdin: a whole source file in base64
        # overflows the command-line length limit
        code, err = _exec(
            f"python3 -c \""
            f"import base64, sys, pathlib; "
            f"pathlib.Path('{filepath}').write_bytes("
            f"base64.b64decode(sys.stdin.read()))\"",
            stdin=encoded,
        )
        return code == 0, err
    path = _resolve(filepath)
    try:
        path.write_text(content)
        return True, ""
    except Exception as exc:
        return False, str(exc)


@mcp.tool()
def read_file(filepath: str, start_line: int = 1, end_line: int = 0) -> str:
    """
    Read a file from the repository with line numbers.

    Args:
        filepath: Absolute or testbed-relative path to the file.
        start_line: First line to read (1-indexed, default 1).
        end_line: Last line to read inclusive (0 = read to end of file).

    Returns:
        File content with each line prefixed "N: content".
    """
    ok, content = _read_raw(filepath)
    if not ok:
        return f"Error: {content}"

    lines = content.splitlines()
    start = max(1, start_line) - 1
    end = end_line if end_line > 0 else len(lines)
    selected = lines[start:end]
    return "\n".join(
        f"{i + start + 1}: {line}" for i, line in enumerate(selected)
    )


@mcp.tool()
def list_files(directory: str = "/testbed",
               pattern: str = "*.py") -> list[str]:
    """
    List files in a directory matching a glob pattern.

    Args:
        directory: Directory to search (default /testbed).
        pattern: Glob pattern for filenames (default *.py).

    Returns:
        Sorted list of filenames relative to the searched directory.
    """
    if _CONTAINER:
        code, output = _exec(
            f"find {directory} -name '{pattern}' -type f | sort"
        )
        if code != 0 or not output.strip():
            return []
        return [
            p.removeprefix(directory).lstrip("/")
            for p in output.strip().splitlines()
        ]
    else:
        base = _resolve(directory)
        try:
            return sorted(
                str(p.relative_to(base))
                for p in base.rglob(pattern)
                if p.is_file()
            )
        except Exception:
            return []


@mcp.tool()
def search_code(pattern: str, file_pattern: str = "*.py",
                around: int = 0) -> str:
    """
    Search for a text pattern across repository files (like grep -rn).

    Args:
        pattern: The text (or regex) to search for.
        file_pattern: Glob to restrict which files to search (default *.py).
        around: How many lines on each side of a match to include, the
            way "grep -C" does (default 0, matching lines only). A few
            lines let one call both locate the code and read its body.

    Returns:
        Matching lines formatted as "filepath:lineno: content". Lines
        shown around a match carry a "-" separator instead of ":".
    """
    base = "/testbed" if _CONTAINER else "."
    ctx = f"-C {around} " if around > 0 else ""
    code, output = _exec(
        f"grep -rn {ctx}--include='{file_pattern}' '{pattern}' {base}",
        workdir=None if _CONTAINER else _TESTBED,
    )
    if not output.strip() and "/" in file_pattern:
        # a path in file_pattern never matches: the filter only sees
        # file names, and telling the model saves it blind retries
        return ("No matches found. file_pattern filters by file name "
                "only, not by path. Use a plain name like 'vector.py' "
                "or '*.py'.")
    return output.strip() or "No matches found."


@mcp.tool()
def search_function_or_class_definition_in_code(name: str) -> str:
    """
    Find where a function or class is defined across the repository.

    Args:
        name: The function or class name to find.

    Returns:
        Matching definition lines formatted as "filepath:lineno: content".
    """
    escaped = re.escape(name)
    base = "/testbed" if _CONTAINER else "."
    code, output = _exec(
        f"grep -rn -E '^[[:space:]]*(def|class)[[:space:]]+{escaped}' {base}",
        workdir=None if _CONTAINER else _TESTBED,
    )
    return output.strip() or f"No definition of '{name}' found."


@mcp.tool()
def find_references(name: str, filepath: str = "", line: int = 0) -> str:
    """
    Find all references to a symbol in the repository.

    Args:
        name: The symbol name to search for.
        filepath: Restrict search to this file (empty = search all files).
        line: Hint line number (kept for API compatibility, not used).

    Returns:
        All lines containing the symbol, with file and line number.
    """
    if filepath:
        if _CONTAINER:
            code, output = _exec(f"grep -Hn '{name}' {filepath}")
        else:
            code, output = _exec(f"grep -Hn '{name}' {_resolve(filepath)}")
    else:
        base = "/testbed" if _CONTAINER else "."
        code, output = _exec(
            f"grep -rn '{name}' {base}",
            workdir=None if _CONTAINER else _TESTBED,
        )
    return output.strip() or f"No references to '{name}' found."


def _nearest_region(content: str, old_str: str) -> str:
    """
    Find the file region that most resembles a failed old_str.

    A mismatch usually means the string was retyped from memory with
    the wrong whitespace. Showing the real text around the intended
    spot lets the next attempt copy it exactly instead of guessing.
    """
    probe = next(
        (ln.strip() for ln in old_str.splitlines() if ln.strip()), "")
    if not probe:
        return ""
    lines = content.splitlines()
    idx = next((i for i, ln in enumerate(lines) if probe in ln), -1)
    if idx < 0:
        close = difflib.get_close_matches(
            probe, [ln.strip() for ln in lines], n=1, cutoff=0.6)
        if not close:
            return ""
        idx = next(
            i for i, ln in enumerate(lines) if ln.strip() == close[0])
    lo, hi = max(0, idx - 3), min(len(lines), idx + 4)
    header = f"The file around lines {lo + 1}-{hi} reads:\n"
    return header + "\n".join(lines[lo:hi])


@mcp.tool()
def edit_file(filepath: str, old_str: str, new_str: str) -> str:
    """
    Replace the first occurrence of a string in a file.

    Args:
        filepath: Absolute or testbed-relative path to the file.
        old_str: The exact string to replace.
        new_str: The replacement string.

    Returns:
        A confirmation message or an error description.
    """
    ok, content = _read_raw(filepath)
    if not ok:
        return f"Error reading file: {content}"
    if old_str not in content:
        msg = (f"String not found in {filepath}. old_str must match "
               f"the file exactly, whitespace included.")
        region = _nearest_region(content, old_str)
        return f"{msg}\n{region}" if region else msg
    new_content = content.replace(old_str, new_str, 1)
    ok, err = _write_raw(filepath, new_content)
    if not ok:
        return f"Error writing file: {err}"
    global _edited_since_test
    _edited_since_test = True
    return f"OK: replaced in {filepath}."


@mcp.tool()
def run_command(command: str, workdir: str = "/testbed") -> str:
    """
    Run an arbitrary shell command inside the repository.

    Args:
        command: Shell command to execute.
        workdir: Working directory for the command (default /testbed).

    Returns:
        Combined stdout and stderr of the command.
    """
    resolved = workdir if _CONTAINER else str(_resolve(workdir))
    code, output = _exec(command, workdir=resolved)
    return output.strip() or "(no output)"


_FAIL_MARKS = ("FAILED", " failed", "exceptions", "DO *NOT* COMMIT")


def _test_verdict(output: str) -> str:
    """The verdict line for a test run, or nothing when the format
    is unknown. Failure marks win over pass marks."""
    if any(mark in output for mark in _FAIL_MARKS):
        return ("\n[run_tests] Tests failed. Fix the code and call "
                "run_tests() again.")
    pytest_pass = re.search(r"\d+ passed", output)
    django_pass = re.search(r"Ran \d+ tests?", output) and "\nOK" in output
    if pytest_pass or django_pass:
        return "\n[run_tests] All tests passed."
    return ""


@mcp.tool()
def run_tests() -> str:
    """
    Run the task's test suite against the current state of the repo.

    Returns:
        Combined stdout and stderr of the eval script.
    """
    global _edited_since_test
    _edited_since_test = False
    if not _EVAL_SCRIPT:
        return "No eval script configured (SWEBENCH_EVAL_SCRIPT not set)."
    if _CONTAINER:
        # 2>&1 keeps stdout and stderr in emission order, so the summary
        # reads in sequence instead of after a separate stderr block
        code, output = _exec(f"bash {_EVAL_SCRIPT} 2>&1", timeout=300)
    else:
        try:
            r = subprocess.run(
                ["bash", _EVAL_SCRIPT],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=300,
            )
            output = r.stdout
        except subprocess.TimeoutExpired:
            output = "Eval script timed out (>300s)."
        except Exception as exc:
            output = str(exc)
    # lines starting with one or more + are shell trace, not test results
    output = "\n".join(line for line in output.splitlines()
                       if not re.match(r"\++ ", line))
    verdict = _test_verdict(output)
    global _tests_failed
    _tests_failed = "Tests failed" in verdict
    return (output.strip() or "(no output)") + verdict


@mcp.tool()
def get_patch() -> str:
    """
    Return the current git diff of the repository as a unified patch.

    Returns:
        The unified git diff, or a message if there are no changes.
    """
    if _edited_since_test:
        raise RuntimeError(
            "the repository changed after the last run_tests. Run the "
            "tests first and read their result, then collect the patch.")
    if _tests_failed:
        raise RuntimeError(
            "the last run_tests failed. Fix the code, get a passing "
            "run, then collect the patch.")
    workdir = "/testbed" if _CONTAINER else None
    code, output = _exec("git diff", workdir=workdir or _TESTBED)
    patch = output.strip()
    if not patch:
        return "No changes (empty diff)."
    # a unified diff must end with a newline, git apply rejects the
    # file as corrupt without one
    return patch + "\n"


if __name__ == "__main__":
    mcp.run(transport="stdio")
