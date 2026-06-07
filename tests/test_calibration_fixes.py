"""Tests for bug-fix commit mining."""

from __future__ import annotations

import subprocess
from pathlib import Path

from bin.calibration.fixes import FixCommit, is_fix_subject, mine_fix_commits, parse_log


def test_keyword_classification_word_boundary() -> None:
    assert is_fix_subject("fix: handle empty input")
    assert is_fix_subject("Fixed a crash")
    assert is_fix_subject("resolve flaky test")
    assert is_fix_subject("bugfix for parser")
    # Word-boundary: "prefix"/"suffix" must NOT count as a fix.
    assert not is_fix_subject("add prefix to keys")
    assert not is_fix_subject("refactor suffix handling")
    assert not is_fix_subject("add a feature")


def test_parse_log_fields_issues_and_merge() -> None:
    sha1, sha2, sha3 = "a" * 40, "b" * 40, "c" * 40
    stdout = (
        f"{sha1}\x00{'d' * 40}\x00fix: off-by-one (closes #12)\n"
        f"{sha2}\x00{'e' * 40} {'f' * 40}\x00fix: merge branch with bug repair\n"
        f"{sha3}\x00{'g' * 40}\x00feat: add new endpoint\n"
    )
    fixes = parse_log(stdout)
    assert [f.sha for f in fixes] == [sha1, sha2]  # feat dropped
    assert fixes[0].issues == (12,)
    assert fixes[0].is_merge is False
    assert fixes[1].is_merge is True  # two parents


def test_mine_fix_commits_uses_injected_runner() -> None:
    seen: list[list[str]] = []

    def _fake(argv: list[str], cwd: Path | None, timeout: int) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(
            argv, 0, stdout=f"{'a' * 40}\x00{'b' * 40}\x00fix: thing\n", stderr=""
        )

    fixes = mine_fix_commits(Path("/repo"), since_sha="OLD", until_sha="HEAD", paths=("src",), run=_fake)
    assert fixes == [FixCommit(sha="a" * 40, subject="fix: thing", issues=(), is_merge=False)]
    # The window + path scoping made it into the git argv.
    assert seen[0][:5] == ["git", "-C", "/repo", "log", "OLD..HEAD"]
    assert seen[0][-2:] == ["--", "src"]


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=root, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=root, check=True)


def test_mine_fix_commits_real_repo(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "m.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "feat: init"], cwd=tmp_path, check=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()
    (tmp_path / "m.py").write_text("x = 2\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-aqm", "fix: correct value"], cwd=tmp_path, check=True)

    fixes = mine_fix_commits(tmp_path, since_sha=base, until_sha="HEAD", paths=())
    assert [f.subject for f in fixes] == ["fix: correct value"]
