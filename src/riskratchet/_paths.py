"""Path helpers shared by language backends (Python `analysis`, TypeScript `typescript`).

These were originally private to `analysis.py`; they are promoted here so the TypeScript
backend can reuse them without reaching into another module's private API (see
`docs/language-backend-contract.md` — the seam should not be crossed through privates).
`analysis.py` re-exports them under its historical `_`-prefixed names for back-compat.
"""

from __future__ import annotations

import os
from fnmatch import fnmatch
from pathlib import Path


def relative_posix(path: Path, root: Path) -> str:
    """Key a file by its path relative to `root`: one spelling per file (0.3.6).

    In order: the path as given, joined to the cwd and normalised lexically, relative to
    `root` — so a symlinked scan root keeps the key its spelling gives (`src/x.py`, not
    the symlink's target); the resolved path relative to the resolved root; a `../`
    path for a file outside the root; and, when even that is impossible (another drive
    on Windows), the path as given. Before 0.3.6 the last step ran for every
    out-of-root file, so the same file had one key per cwd and spelling, and the SARIF
    `uri` re-derived a different one against the process cwd.
    """
    try:
        return Path(os.path.normpath(Path.cwd() / path)).relative_to(root).as_posix()
    except ValueError:
        pass
    resolved, resolved_root = path.resolve(), root.resolve()
    try:
        return resolved.relative_to(resolved_root).as_posix()
    except ValueError:
        pass
    try:
        return Path(os.path.relpath(resolved, resolved_root)).as_posix()
    except ValueError:  # Windows: a different drive has no relative path
        return path.as_posix()


def has_hidden_parent(path: Path, root: Path) -> bool:
    return any(part.startswith(".") for part in path.relative_to(root).parts[:-1])


def any_match(value: str, patterns: list[str]) -> bool:
    return any(fnmatch(value, pattern) for pattern in patterns)
