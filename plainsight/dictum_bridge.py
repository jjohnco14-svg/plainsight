"""Dictum's syntax-tree node types and type vocabulary.

Uses the Dictum compiler from the repo when Plainsight sits inside it (or
$DICTUM_COMPILER points at it); otherwise the bundled copies in _dictum/,
so `pip install` works on its own in any sandbox.
"""
import os
import sys
from pathlib import Path

_REL = Path("dictum-development/scripts/dictum_compiler/compiler")


def _find_compiler_dir():
    env = os.environ.get("DICTUM_COMPILER")
    if env and (Path(env) / "dictumc").is_dir():
        return Path(env)
    for parent in Path(__file__).resolve().parents:
        cand = parent / _REL
        if (cand / "dictumc" / "ast_nodes.py").is_file():
            return cand
    return None


_dir = _find_compiler_dir()
if _dir is not None:
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))
    from dictumc import ast_nodes as A  # noqa: E402
    from dictumc import type_registry as TR  # noqa: E402
else:
    from ._dictum import ast_nodes as A  # noqa: E402,F401
    from ._dictum import type_registry as TR  # noqa: E402,F401

COMPILER_DIR = _dir
