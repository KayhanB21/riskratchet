"""Tests for the shared git_checkout facade."""

from __future__ import annotations

import subprocess
from pathlib import Path

from bin.calibration import git_checkout


def test_git_wrapper_builds_dash_c_argv() -> None:
    seen: list[list[str]] = []

    def _fake(argv: list[str], cwd: Path | None, timeout: int) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, returncode=0, stdout="ok", stderr="")

    result = git_checkout.git(["rev-parse", "HEAD"], Path("/repo"), run=_fake)
    assert result.stdout == "ok"
    assert seen == [["git", "-C", "/repo", "rev-parse", "HEAD"]]


def test_worktree_for_under_cache(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from bin.calibration import coverage_replay

    monkeypatch.setattr(coverage_replay, "CACHE_DIR", Path("/tmp/_cache"))
    wt = git_checkout.worktree_for("requests", "abcdef1234567890")
    assert wt == Path("/tmp/_cache/requests/abcdef123456/worktree")


def test_reexports_present() -> None:
    # The SZZ modules import these from git_checkout.
    assert git_checkout.ensure_clone is not None
    assert git_checkout.revision_cache_dir is not None
    assert git_checkout.default_runner is not None
