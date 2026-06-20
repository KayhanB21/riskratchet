"""Path helpers shared by language backends (Python `analysis`, TypeScript `typescript`).

These were originally private to `analysis.py`; they are promoted here so the TypeScript
backend can reuse them without reaching into another module's private API (see
`docs/language-backend-contract.md` — the seam should not be crossed through privates).
`analysis.py` re-exports them under its historical `_`-prefixed names for back-compat.
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path


def relative_posix(path: Path, root: Path) -> str:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        rel = path
    return rel.as_posix()


def has_hidden_parent(path: Path, root: Path) -> bool:
    return any(part.startswith(".") for part in path.relative_to(root).parts[:-1])


def any_match(value: str, patterns: list[str]) -> bool:
    return any(fnmatch(value, pattern) for pattern in patterns)
