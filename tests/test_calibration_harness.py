"""End-to-end (but hermetic) tests for the calibration harness CLI.

No network, no gh, no clone: PR enumeration and per-revision replay are
monkeypatched with canned in-process reports, and all output paths are redirected
into tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bin.calibration import coverage_replay, harness
from bin.calibration.config import PrLabel
from bin.calibration.corpus import analyze_report
from bin.calibration.coverage_replay import RevisionResult
from bin.calibration.prs import PrRef
from bin.calibration.serial import report_to_dict

_SIMPLE = "def f(items):\n    return sum(items)\n"
_GNARLY = (
    "def f(items):\n    total = 0\n    for it in items:\n        if it > 0:\n            total += it\n"
    "        elif it < 0:\n            total -= it\n        else:\n            total += 1\n"
    "        if total > 100:\n            total = 100\n        if it % 2 == 0:\n            total += 2\n"
    "    return total\n"
)


def _report(tmp: Path, body: str):  # type: ignore[no-untyped-def]
    src = tmp / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "m.py").write_text(body, encoding="utf-8")
    return analyze_report([src], tmp)


def _redirect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(harness, "ROLLUP_PATH", tmp_path / "pr-replay-rollup.json")
    monkeypatch.setattr(harness, "CANDIDATES_PATH", tmp_path / "sprawl-candidates.json")
    monkeypatch.setattr(coverage_replay, "CACHE_DIR", tmp_path / "_cache")


def test_replay_then_rescore_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _redirect(tmp_path, monkeypatch)

    base_report = _report(tmp_path / "base", _SIMPLE)
    head_report = _report(tmp_path / "head", _GNARLY)

    # One merged PR for the (real, enabled) requests config.
    pr = PrRef(repo="requests", number=1, base_sha="b" * 40, head_sha="h" * 40, merge_commit="m" * 40)
    monkeypatch.setattr(harness, "enumerate_merged_prs", lambda repo, max_prs: [pr])

    reports = {"b" * 40: base_report, "h" * 40: head_report}

    def _fake_replay(repo, sha, *, force=False):  # type: ignore[no-untyped-def]
        # Also seed the analyze cache so rescore can find it.
        cache = coverage_replay.revision_cache_dir(repo.name, sha)
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "analyze.json").write_text(json.dumps(report_to_dict(reports[sha])), encoding="utf-8")
        return RevisionResult(sha, reports[sha], None, 0, 0, True, cached=True)

    monkeypatch.setattr(harness, "replay_revision", _fake_replay)
    # Label the PR as rejected so rescore has a labelled separation sample.
    label = PrLabel(repo="requests", pr=1, base_sha="b" * 40, head_sha="h" * 40, label="rejected")
    monkeypatch.setattr(harness, "load_labels", lambda: [label])

    assert harness.main(["replay", "--repos", "requests", "--max-prs", "1"]) == 0

    rollup = json.loads(harness.ROLLUP_PATH.read_text())
    assert rollup["summary"]["n_prs"] == 1
    assert rollup["summary"]["n_labeled"] == 1
    (record,) = rollup["records"]
    assert record["repo"] == "requests"
    assert record["label"] == "rejected"
    assert record["regression_count"] >= 1

    # rescore reads the rollup + the seeded analyze cache.
    assert harness.main(["rescore"]) == 0
    candidates = json.loads(harness.CANDIDATES_PATH.read_text())
    assert candidates["n_labeled_prs"] == 1
    keys = {c["candidate"] for c in candidates["candidates"]}
    assert {"baseline", "drop_file_line", "shrink_file_share", "raise_band"} == keys


def test_rescore_without_rollup_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _redirect(tmp_path, monkeypatch)
    assert harness.main(["rescore"]) == 1


def test_replay_unknown_repo_selection_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _redirect(tmp_path, monkeypatch)
    assert harness.main(["replay", "--repos", "nonesuch"]) == 1
