"""MCP tool server for MBPP: runs the task assertions against the code.

The single tool executes the candidate solution and the task's
assertions in a separate process and returns, as JSON, whether they
all passed together with the output to show the agent.
"""

import json
import subprocess
import sys
import textwrap

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("agent-smith-mbpp")


@mcp.tool()
def run_tests(code: str, test_list: list[str]) -> str:
    """Run the task assertions against code. Returns JSON: success, output."""
    # Build a self-contained script: define the function, then run the
    # assertions one at a time. A bare assert carries no message, so
    # each one is named as it runs and the failing line is reported.
    checks = "\n".join(
        f"_tests.append({test!r})" for test in test_list
    )
    script = textwrap.dedent(f"""\
        import sys, traceback
        _tests = []
        try:
{textwrap.indent(code, "            ")}
{textwrap.indent(checks, "            ")}
            for _t in _tests:
                try:
                    exec(_t)
                except AssertionError:
                    _got = ""
                    if "==" in _t:
                        try:
                            _call = _t.split("assert", 1)[1]
                            _got = " -> got " + repr(eval(
                                _call.split("==")[0].strip()))
                        except Exception:
                            pass
                    print("Test failed: " + _t + _got)
                    sys.exit(1)
            print("__ok__")
        except Exception:
            traceback.print_exc()
    """)

    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=15,
        )
        combined = (result.stdout + result.stderr).strip()
        success = "__ok__" in result.stdout and result.returncode == 0
        # Strip the internal sentinel from the output the LLM sees
        output = combined.replace("__ok__", "").strip()
    except subprocess.TimeoutExpired:
        success = False
        output = "Test execution timed out (>15 s)."
    except Exception as exc:
        success = False
        output = f"run_tests error: {exc}"

    return json.dumps({"success": success, "output": output})


if __name__ == "__main__":
    mcp.run(transport="stdio")
