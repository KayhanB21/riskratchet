"""Tests for the in-process PR-replay core and gh-based PR enumeration.

Hermetic: no network, no `gh`, no cloning. The replay core is exercised against
two on-disk source trees (a stand-in for a checked-out base/head pair) plus
hand-written coverage, mirroring the `tests/fixtures/**/coverage.json` precedent.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from bin.calibration.config import PrLabel
from bin.calibration.corpus import analyze_report
from bin.calibration.prs import PrRef, enumerate_merged_prs, parse_pr_list, repo_slug
from bin.calibration.replay import join_label, replay_paths

_SIMPLE = "def process(items):\n    return sum(items)\n"

_GNARLY = (
    "def process(items):\n"
    "    total = 0\n"
    "    for it in items:\n"
    "        if it > 0:\n"
    "            total += it\n"
    "        elif it < 0:\n"
    "            total -= it\n"
    "        elif it == 0:\n"
    "            total += 1\n"
    "        if total > 100:\n"
    "            total = 100\n"
    "        if total < -100:\n"
    "            total = -100\n"
    "        if it % 2 == 0:\n"
    "            total += 2\n"
    "        elif it % 3 == 0:\n"
    "            total += 3\n"
    "        while total > 1000:\n"
    "            total -= 10\n"
    "    if not items:\n"
    "        return 0\n"
    "    return total\n"
)


def _tree(root: Path, body: str, *, name: str = "m.py") -> Path:
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / name).write_text(body, encoding="utf-8")
    return src


# --- replay core ---------------------------------------------------------


def test_replay_detects_regression(tmp_path: Path) -> None:
    base_root = tmp_path / "base"
    head_root = tmp_path / "head"
    _tree(base_root, _SIMPLE)
    _tree(head_root, _GNARLY)

    record = replay_paths(
        repo="demo",
        pr=1,
        base_sha="b" * 40,
        head_sha="h" * 40,
        base_paths=[base_root / "src"],
        base_root=base_root,
        head_paths=[head_root / "src"],
        head_root=head_root,
        fail_regression_above=5.0,
    )
    assert record.regression_count == 1
    (regressed,) = record.regressed
    assert regressed.target.endswith("m.py::process")
    assert regressed.delta > 5.0
    assert record.head_sha == "h" * 40


def test_replay_digest_is_stable(tmp_path: Path) -> None:
    base_root = tmp_path / "base"
    head_root = tmp_path / "head"
    _tree(base_root, _SIMPLE)
    _tree(head_root, _GNARLY)
    kwargs = dict(
        repo="demo",
        pr=7,
        base_sha="0" * 40,
        head_sha="1" * 40,
        base_paths=[base_root / "src"],
        base_root=base_root,
        head_paths=[head_root / "src"],
        head_root=head_root,
    )
    first = replay_paths(**kwargs).to_digest()  # type: ignore[arg-type]
    second = replay_paths(**kwargs).to_digest()  # type: ignore[arg-type]
    assert first == second
    # Short SHAs, sorted regressed list.
    assert first["base_sha"] == "0" * 12
    assert first["pr"] == 7


def test_replay_detects_moved(tmp_path: Path) -> None:
    # Identical function body, moved to a different file => MOVED with confidence.
    base_root = tmp_path / "base"
    head_root = tmp_path / "head"
    _tree(base_root, _GNARLY, name="a.py")
    _tree(head_root, _GNARLY, name="b.py")

    record = replay_paths(
        repo="demo",
        pr=2,
        base_sha="b" * 40,
        head_sha="h" * 40,
        base_paths=[base_root / "src"],
        base_root=base_root,
        head_paths=[head_root / "src"],
        head_root=head_root,
    )
    assert record.regression_count == 0
    assert record.moved_count == 1
    assert record.match_confidences
    assert all(0.0 <= c <= 1.0 for c in record.match_confidences)


def test_coverage_path_is_consumed(tmp_path: Path) -> None:
    # A fully-covered function scores lower than the same function with no
    # coverage (pessimistic), proving coverage_path is plumbed through.
    root = tmp_path / "p"
    _tree(root, _GNARLY)
    cov = tmp_path / "coverage.json"
    n_lines = len(_GNARLY.splitlines())
    cov.write_text(
        json.dumps(
            {"files": {"src/m.py": {"executed_lines": list(range(1, n_lines + 1)), "missing_lines": []}}}
        ),
        encoding="utf-8",
    )
    covered = analyze_report([root / "src"], root, coverage_path=cov).functions[0].score
    uncovered = analyze_report([root / "src"], root).functions[0].score
    assert covered < uncovered


# --- label join ----------------------------------------------------------


def _record(tmp_path: Path):  # type: ignore[no-untyped-def]
    base_root = tmp_path / "base"
    head_root = tmp_path / "head"
    _tree(base_root, _SIMPLE)
    _tree(head_root, _GNARLY)
    return replay_paths(
        repo="demo",
        pr=5,
        base_sha="b" * 40,
        head_sha="h" * 40,
        base_paths=[base_root / "src"],
        base_root=base_root,
        head_paths=[head_root / "src"],
        head_root=head_root,
    )


def test_join_label_exact(tmp_path: Path) -> None:
    rec = _record(tmp_path)
    labels = [PrLabel(repo="demo", pr=5, base_sha="b" * 40, head_sha="h" * 40, label="accepted")]
    joined = join_label(rec, labels)
    assert joined.label == "accepted"
    assert joined.label_stale is False


def test_join_label_stale_on_sha_mismatch(tmp_path: Path) -> None:
    rec = _record(tmp_path)
    labels = [PrLabel(repo="demo", pr=5, base_sha="x" * 40, head_sha="y" * 40, label="rejected")]
    joined = join_label(rec, labels)
    assert joined.label == "unlabeled"
    assert joined.label_stale is True


def test_join_label_unlabeled(tmp_path: Path) -> None:
    rec = _record(tmp_path)
    joined = join_label(rec, [])
    assert joined.label == "unlabeled"
    assert joined.label_stale is False


# --- PR enumeration ------------------------------------------------------


def test_repo_slug() -> None:
    assert repo_slug("https://github.com/psf/requests") == "psf/requests"
    assert repo_slug("https://github.com/encode/httpx.git") == "encode/httpx"
    assert repo_slug("https://github.com/datastax/python-driver/") == "datastax/python-driver"


def test_parse_pr_list_skips_rows_without_shas() -> None:
    # Mirrors the real `gh pr list --json ...,mergeCommit` shape: mergeCommit is
    # an object {"oid": ...} (or null for squash/rebase merges).
    stdout = json.dumps(
        [
            {"number": 1, "baseRefOid": "a", "headRefOid": "b", "mergeCommit": {"oid": "m"}},
            {"number": 2, "baseRefOid": "", "headRefOid": "b", "mergeCommit": {"oid": "m"}},
            {"number": 3, "baseRefOid": "a", "headRefOid": None, "mergeCommit": {"oid": "m"}},
            {"number": 4, "baseRefOid": "a", "headRefOid": "b", "mergeCommit": None},
        ]
    )
    refs = parse_pr_list("demo", stdout)
    assert refs == [
        PrRef(repo="demo", number=1, base_sha="a", head_sha="b", merge_commit="m"),
        PrRef(repo="demo", number=4, base_sha="a", head_sha="b", merge_commit=""),
    ]


def test_parse_pr_list_malformed_json_is_empty() -> None:
    assert parse_pr_list("demo", "not json") == []


def test_enumerate_degrades_when_gh_missing() -> None:
    from bin.calibration.config import RepoConfig

    repo = RepoConfig(
        name="demo",
        url="https://github.com/x/y",
        test_command="pytest --cov-report=json:{coverage_out}",
        coverage_prefix="y",
    )

    def _missing(_argv: list[str]) -> str:
        raise FileNotFoundError

    assert enumerate_merged_prs(repo, 5, runner=_missing) == []


def test_enumerate_degrades_on_called_process_error() -> None:
    from bin.calibration.config import RepoConfig

    repo = RepoConfig(
        name="demo",
        url="https://github.com/x/y",
        test_command="pytest --cov-report=json:{coverage_out}",
        coverage_prefix="y",
    )

    def _fail(argv: list[str]) -> str:
        raise subprocess.CalledProcessError(1, argv, stderr="boom")

    assert enumerate_merged_prs(repo, 5, runner=_fail) == []
