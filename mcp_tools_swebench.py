"""Alexandre - SWE-bench MCP tool server: repository inspection and editing tools."""

import base64
import os
import re
import subprocess
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("agent-smith-swebench")

# ---------------------------------------------------------------------------
# Runtime context
# ---------------------------------------------------------------------------
# Exam mode:        TESTBED_PATH=/path/to/local/testbed
# Real SWE-bench:   SWEBENCH_CONTAINER=<docker-cid>
#                   SWEBENCH_EVAL_SCRIPT=/path/to/eval.sh

_CONTAINER: str = os.environ.get("SWEBENCH_CONTAINER", "")
_EVAL_SCRIPT: str = os.environ.get("SWEBENCH_EVAL_SCRIPT", "")
_TESTBED: str = os.environ.get("TESTBED_PATH", "/testbed")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _exec(cmd: str, workdir: str | None = None) -> tuple[int, str]:
    """
    Run a shell command in the testbed context.

    If SWEBENCH_CONTAINER is set, execute inside the Docker container.
    Otherwise, run directly as a subprocess in the local testbed directory.
    """
    try:
        if _CONTAINER:
            cwd = workdir or "/testbed"
            r = subprocess.run(
                ["docker", "exec", "-w", cwd, _CONTAINER, "bash", "-c", cmd],
                capture_output=True, text=True, timeout=60,
            )
        else:
            cwd = workdir or _TESTBED
            r = subprocess.run(
                cmd, shell=True, cwd=cwd,
                capture_output=True, text=True, timeout=60,
            )
        return r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return 1, "Command timed out."
    except Exception as exc:
        return 1, str(exc)


def _resolve(filepath: str) -> Path:
    """Resolve a path relative to the testbed when not absolute."""
    p = Path(filepath)
    return p if p.is_absolute() else Path(_TESTBED) / p


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
        code, err = _exec(
            f"python3 -c \""
            f"import base64, pathlib; "
            f"pathlib.Path('{filepath}').write_bytes("
            f"base64.b64decode('{encoded}'))\""
        )
        return code == 0, err
    path = _resolve(filepath)
    try:
        path.write_text(content)
        return True, ""
    except Exception as exc:
        return False, str(exc)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

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
    return "\n".join(f"{i + start + 1}: {line}" for i, line in enumerate(selected))


@mcp.tool()
def list_files(directory: str = "/testbed", pattern: str = "*.py") -> list[str]:
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
def search_code(pattern: str, file_pattern: str = "*.py") -> str:
    """
    Search for a text pattern across repository files (like grep -rn).

    Args:
        pattern: The text (or regex) to search for.
        file_pattern: Glob to restrict which files to search (default *.py).

    Returns:
        Matching lines formatted as "filepath:lineno: content".
    """
    base = "/testbed" if _CONTAINER else "."
    code, output = _exec(
        f"grep -rn --include='{file_pattern}' '{pattern}' {base}",
        workdir=None if _CONTAINER else _TESTBED,
    )
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
            code, output = _exec(f"grep -n '{name}' {filepath}")
        else:
            code, output = _exec(f"grep -n '{name}' {_resolve(filepath)}")
    else:
        base = "/testbed" if _CONTAINER else "."
        code, output = _exec(
            f"grep -rn '{name}' {base}",
            workdir=None if _CONTAINER else _TESTBED,
        )
    return output.strip() or f"No references to '{name}' found."


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
        return f"String not found in {filepath}."
    new_content = content.replace(old_str, new_str, 1)
    ok, err = _write_raw(filepath, new_content)
    if not ok:
        return f"Error writing file: {err}"
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
    code, output = _exec(command, workdir=workdir if _CONTAINER else _resolve(workdir))
    return output.strip() or "(no output)"


@mcp.tool()
def run_tests() -> str:
    """
    Run the task's evaluation test suite against the current state of the repo.

    Returns:
        Combined stdout and stderr of the eval script.
    """
    if not _EVAL_SCRIPT:
        return "No eval script configured (SWEBENCH_EVAL_SCRIPT not set)."
    if _CONTAINER:
        code, output = _exec(f"bash {_EVAL_SCRIPT}")
    else:
        try:
            r = subprocess.run(
                ["bash", _EVAL_SCRIPT],
                capture_output=True, text=True, timeout=300,
            )
            output = r.stdout + r.stderr
        except subprocess.TimeoutExpired:
            output = "Eval script timed out (>300s)."
        except Exception as exc:
            output = str(exc)
    return output.strip() or "(no output)"


@mcp.tool()
def get_patch() -> str:
    """
    Return the current git diff of the repository as a unified patch.

    Returns:
        The unified git diff, or a message if there are no changes.
    """
    workdir = "/testbed" if _CONTAINER else None
    code, output = _exec("git diff", workdir=workdir or _TESTBED)
    return output.strip() or "No changes (empty diff)."


if __name__ == "__main__":
    mcp.run(transport="stdio")
