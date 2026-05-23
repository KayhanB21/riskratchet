"""Tests for the git-backed churn provider.

`collect_file_churn` shells out to `git log`, so the test sets up real git
repositories in a temporary directory instead of mocking subprocess.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from riskratchet.git import churn_for_file, collect_file_churn


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=root, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=root, check=True)


def _commit(root: Path, relative: str, body: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    subprocess.run(["git", "add", relative], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"touch {relative}"], cwd=root, check=True)


def test_collect_returns_empty_when_disabled(tmp_path: Path) -> None:
    assert collect_file_churn(tmp_path, enabled=False) == {}


def test_collect_returns_empty_without_git_dir(tmp_path: Path) -> None:
    assert collect_file_churn(tmp_path) == {}


def test_collect_counts_commits_per_file(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _commit(tmp_path, "a.py", "x = 1\n")
    _commit(tmp_path, "a.py", "x = 2\n")
    _commit(tmp_path, "b.py", "y = 1\n")
    counts = collect_file_churn(tmp_path)
    assert counts.get("a.py", 0) == 2
    assert counts.get("b.py", 0) == 1


def test_collect_returns_empty_when_git_log_fails(tmp_path: Path, monkeypatch) -> None:
    # An empty repo (no .git) returns {}; verified above. Here we point to a
    # path that has a .git file (not a directory) so git log fails with a
    # non-zero status. That hits the `result.returncode != 0` branch.
    (tmp_path / ".git").write_text("not a real git dir", encoding="utf-8")
    counts = collect_file_churn(tmp_path)
    assert counts == {}


def test_churn_for_file_lookups_default_zero() -> None:
    stats = churn_for_file({"a.py": 3}, "a.py")
    assert stats.commits == 3
    assert churn_for_file({"a.py": 3}, "missing.py").commits == 0
