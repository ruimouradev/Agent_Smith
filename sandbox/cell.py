"""Sandbox child process: runs the agent's code under the restrictions.

Installs the import allowlist, swaps in safe builtins and a path-checked
open, wires up the MCP tool wrappers, then executes the code the
supervisor feeds on stdin.
"""

import ast
import sys
import os
from contract import feedback

# the dunder attributes ordinary code names explicitly, every other
# attribute starting with "_" is refused before the code runs
ALLOWED_DUNDERS = {
    "__init__", "__name__", "__doc__", "__len__", "__str__", "__repr__",
    "__eq__", "__lt__", "__hash__", "__iter__", "__next__", "__getitem__",
    "__setitem__", "__contains__", "__call__", "__enter__", "__exit__",
}


class SandboxImportBlocker:
    """
    A sys.meta_path finder that blocks imports unless they match the allowlist.
    """
    def __init__(self, allowed):
        self.allowed = allowed

    def find_spec(self, fullname, path, target=None):
        if self._is_allowed(fullname):
            return None  # Let the normal import system handle it

        raise ModuleNotFoundError(
            feedback.BLOCKED_IMPORT.format(name=fullname,
                                           allowed=", ".join(self.allowed))
        )

    def _is_allowed(self, name):
        for pattern in self.allowed:
            if pattern.endswith(".*"):
                if name == pattern[:-2] or name.startswith(pattern[:-1] + "."):
                    return True
            elif name == pattern:
                return True
        return False


def is_path_allowed(requested_path, allowed_directories):
    try:
        # Resolve real absolute path to avoid traversal attacks
        real_path = os.path.realpath(requested_path)
    except Exception:
        return False

    for allowed_dir in allowed_directories:
        real_allowed = os.path.realpath(allowed_dir)
        # Check if the resolved path is within the allowed directory
        if os.path.commonpath([real_path, real_allowed]) == real_allowed:
            return True
    return False


def secure_open(allowed_directories):
    # Get original open
    import builtins
    original_open = builtins.open

    def _safe_open(file, mode='r', buffering=-1, encoding=None,
                   errors=None, newline=None, closefd=True, opener=None):
        if not is_path_allowed(file, allowed_directories):
            raise PermissionError(
                feedback.BLOCKED_PATH.format(
                    path=file,
                    allowed=", ".join(allowed_directories))
            )
        return original_open(
            file, mode, buffering, encoding, errors,
            newline, closefd, opener)

    return _safe_open


def private_attribute(code):
    """
    The first private attribute the code reaches, or an empty string.

    Private attributes of the allowed modules lead to os and sys, and
    the class hierarchy leads to every loaded type, so both stay out of
    reach. A syntax error is left for exec to report.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return ""
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            if node.attr not in ALLOWED_DUNDERS:
                return node.attr
    return ""


def run_cell():
    import json

    # Read config from environment variable passed by the supervisor
    config_json = os.environ.get('SANDBOX_CONFIG_JSON', '{}')
    config = json.loads(config_json)

    allowed_imports = config.get('authorized_imports', [])
    allowed_directories = config.get('allowed_directories', [])

    # 1. Install Import Blocker
    blocker = SandboxImportBlocker(allowed_imports)
    sys.meta_path.insert(0, blocker)

    # 2. Build Safe Builtins
    import builtins
    safe_builtins = {}

    b_dict = (__builtins__ if isinstance(__builtins__, dict)
              else __builtins__.__dict__)
    # getattr, vars and globals reach attributes by name, which the
    # syntax check on the code cannot see
    dangerous = {"eval", "exec", "compile", "open", "input", "breakpoint",
                 "getattr", "setattr", "delattr", "vars", "globals"}

    for k, v in b_dict.items():
        if k not in dangerous:
            safe_builtins[k] = v

    def _blocked(name):
        def stub(*args, **kwargs):
            raise PermissionError(feedback.BLOCKED_BUILTIN.format(name=name))
        return stub

    for name in dangerous:
        safe_builtins[name] = _blocked(name)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None,
                       fromlist=(), level=0):
        if not blocker._is_allowed(name):
            raise ModuleNotFoundError(
                feedback.BLOCKED_IMPORT.format(
                    name=name, allowed=", ".join(allowed_imports)))
        return real_import(name, globals, locals, fromlist, level)

    safe_builtins["__import__"] = guarded_import

    # Inject secure open
    safe_builtins['open'] = secure_open(allowed_directories)

    # 3. Read Code from Stdin
    code_to_run = sys.stdin.read()

    def final_answer(answer_string):
        print(f"{feedback.FINAL_PREFIX}{answer_string}", file=sys.stdout,
              end="")
        sys.exit(0)

    # 4. Build execution namespace
    execution_namespace = {
        "__builtins__": safe_builtins,
        "final_answer": final_answer,
        "sandbox_manual": os.environ.get("SANDBOX_MANUAL", ""),
    }

    # 5. Inject MCP tool wrappers (if MCP is active)
    _mcp_tools_json = os.environ.get("MCP_TOOLS_JSON", "")
    _mcp_req_fd_str = os.environ.get("MCP_REQ_FD", "")
    _mcp_res_fd_str = os.environ.get("MCP_RES_FD", "")

    if _mcp_tools_json and _mcp_req_fd_str and _mcp_res_fd_str:
        _mcp_tools = json.loads(_mcp_tools_json)
        _req_fd = int(_mcp_req_fd_str)
        _res_fd = int(_mcp_res_fd_str)

        def _pipe_readline(fd):
            """Read one newline-terminated message from a raw pipe FD."""
            buf = b""
            while True:
                ch = os.read(fd, 1)
                if not ch or ch == b"\n":
                    return buf
                buf += ch

        def _make_tool_wrapper(tool_name, req_fd, res_fd, param_names):
            """Return a callable that proxies tool calls through the pipe."""
            def wrapper(*args, **kwargs):
                # positional args map onto the schema's parameter names
                kwargs.update(zip(param_names, args))
                msg = json.dumps(
                    {"name": tool_name, "arguments": kwargs}
                ).encode() + b"\n"
                os.write(req_fd, msg)
                raw = _pipe_readline(res_fd)
                response = json.loads(raw)
                if response.get("ok"):
                    return response["result"]
                err = response.get("error", "unknown error")
                raise RuntimeError(f"Tool '{tool_name}' failed: {err}")
            wrapper.__name__ = tool_name
            return wrapper

        for _tool in _mcp_tools:
            _name = _tool["name"]
            _params = list(_tool.get("inputSchema", {}).get("properties", {}))
            execution_namespace[_name] = _make_tool_wrapper(
                _name, _req_fd, _res_fd, _params)

    # 6. Execute Code
    blocked = private_attribute(code_to_run)
    if blocked:
        raise PermissionError(feedback.BLOCKED_ATTRIBUTE.format(name=blocked))
    try:
        exec(code_to_run, execution_namespace)
    except (KeyboardInterrupt, SystemExit):
        raise  # Must propagate flow control exceptions
    except Exception:
        raise  # Let the supervisor capture the traceback via stderr to stdout


if __name__ == "__main__":
    run_cell()
