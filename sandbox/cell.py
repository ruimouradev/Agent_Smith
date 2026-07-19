"""Alexandre - child process: executes LLM code under the restrictions."""

import sys
import os
from contract import feedback

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

# In the next steps, we will read the config and inject this blocker into sys.meta_path

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
    
    def _safe_open(file, mode='r', buffering=-1, encoding=None, errors=None, newline=None, closefd=True, opener=None):
        if not is_path_allowed(file, allowed_directories):
            raise PermissionError(
                feedback.BLOCKED_PATH.format(path=file,
                                             allowed=", ".join(allowed_directories))
            )
        return original_open(file, mode, buffering, encoding, errors, newline, closefd, opener)
    
    return _safe_open

def run_cell():
    import json
    
    # Read config from environment variable passed by the supervisor
    config_json = os.environ.get('SANDBOX_CONFIG_JSON', '{}')
    config = json.loads(config_json)
    
    allowed_imports = config.get('authorized_imports', [])
    allowed_directories = config.get('allowed_directories', [])
    
    # 1. Install Import Blocker
    sys.meta_path.insert(0, SandboxImportBlocker(allowed_imports))
    
    # 2. Build Safe Builtins
    import builtins
    safe_builtins = {}
    
    b_dict = __builtins__ if isinstance(__builtins__, dict) else __builtins__.__dict__
    dangerous = {"eval", "exec", "compile", "open", "input", "breakpoint"}
    
    for k, v in b_dict.items():
        if k not in dangerous:
            safe_builtins[k] = v

    def _blocked(name):
        def stub(*args, **kwargs):
            raise PermissionError(feedback.BLOCKED_BUILTIN.format(name=name))
        return stub

    for name in dangerous:
        safe_builtins[name] = _blocked(name)

    # Inject secure open
    safe_builtins['open'] = secure_open(allowed_directories)
    
    # 3. Read Code from Stdin
    code_to_run = sys.stdin.read()
    
    def final_answer(answer_string):
        print(f"{feedback.FINAL_PREFIX}{answer_string}", file=sys.stdout, end="")
        sys.exit(0)
    
    # 4. Execute Code
    execution_namespace = {
        "__builtins__": safe_builtins,
        "final_answer": final_answer,
        "sandbox_manual": os.environ.get("SANDBOX_MANUAL", ""),
    }
    
    try:
        exec(code_to_run, execution_namespace)
    except (KeyboardInterrupt, SystemExit):
        raise  # Must propagate flow control exceptions
    except Exception as e:
        # Standard exceptions will naturally propagate to stderr, 
        # but we let them raise so the supervisor can capture the traceback.
        raise

if __name__ == "__main__":
    run_cell()

