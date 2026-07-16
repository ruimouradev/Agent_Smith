"""Alexandre - child process: executes LLM code under the restrictions."""

import sys
import os

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
            f"Import blocked by sandbox: '{fullname}'. "
            f"Allowed modules: {self.allowed}"
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
