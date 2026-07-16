"""Architecture rules enforced as tests, so they cannot rot.

agent/ and sandbox/ must never import each other — they meet only
through contract/. And every module, class and function of the
finished code carries a docstring.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _imports_of(tree: ast.AST) -> list[str]:
    """Every module name imported anywhere in the tree."""
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_agent_and_sandbox_never_import_each_other():
    """The boundary: each side sees only contract/, never the other."""
    crossings = []
    for package, forbidden in (("agent", "sandbox"), ("sandbox", "agent")):
        for file in (ROOT / package).glob("*.py"):
            for name in _imports_of(ast.parse(file.read_text())):
                if name == forbidden or name.startswith(forbidden + "."):
                    crossings.append(f"{file.name} imports {name}")
    assert not crossings, crossings


def test_docstrings_everywhere():
    """Modules, classes and functions of the finished code are all
    documented; empty placeholder files are skipped."""
    files = (list((ROOT / "agent").glob("*.py"))
             + list((ROOT / "contract").glob("*.py"))
             + [ROOT / "agent_mbpp.py", ROOT / "agent_swebench.py"])
    missing = []
    for file in files:
        source = file.read_text()
        if not source.strip():
            continue
        tree = ast.parse(source)
        if ast.get_docstring(tree) is None:
            missing.append(f"{file.name}: module")
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                if ast.get_docstring(node) is None:
                    missing.append(f"{file.name}: {node.name}")
    assert not missing, missing
