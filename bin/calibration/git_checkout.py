"""Shared git checkout + command helpers for the SZZ modules.

The phase-1 clone/cache primitives already live in ``coverage_replay`` (with their
own monkeypatch surface that the phase-1 tests rely on). Rather than move them —
which would shift those test seams — this module re-exports them as the single
import point for the SZZ code (``fixes``/``szz``/``defects``) and adds a thin
``git()`` runner wrapper mirroring ``riskratchet/git.py``'s subprocess shape.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from bin.calibration.coverage_replay import (
    CommandRunner,
    _default_runner,
    ensure_clone,
    revision_cache_dir,
)

__all__ = [
    "CommandRunner",
    "default_runner",
    "ensure_clone",
    "git",
    "revision_cache_dir",
    "worktree_for",
]

# Public alias for the injectable default runner.
default_runner = _default_runner


def git(
    argv: list[str],
    cwd: Path,
    *,
    run: CommandRunner = _default_runner,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    """Run ``git -C <cwd> <argv>`` via the injectable runner.

    Uses ``-C`` (like ``riskratchet/git.py``) so the runner's own ``cwd`` stays
    unused; tests inject a fake ``run`` and assert on the argv.
    """
    return run(["git", "-C", str(cwd), *argv], None, timeout)


def worktree_for(repo_name: str, sha: str) -> Path:
    """Per-SHA worktree path under the gitignored replay cache."""
    return revision_cache_dir(repo_name, sha) / "worktree"
