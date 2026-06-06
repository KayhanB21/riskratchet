"""Hermetic CLI tests for the `defects` and `predict` subcommands.

collect_defect_labels (clone/score/blame) is monkeypatched; the snapshot analyze
cache and output paths are redirected into tmp_path. No network/gh/clone.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bin.calibration import coverage_replay, harness
from bin.calibration.corpus import analyze_report
from bin.calibration.defects import DefectLabels, SnapshotPopulation
from bin.calibration.serial import report_to_dict

_SNAP = "S" * 40


def _snapshot(tmp_path: Path) -> SnapshotPopulation:
    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "m.py").write_text("def f(x):\n    return x + 1\n", encoding="utf-8")
    return SnapshotPopulation(_SNAP, analyze_report([src], tmp_path))


def _labels(snapshot: SnapshotPopulation) -> DefectLabels:
    target = snapshot.report.functions[0].id
    return DefectLabels(
        repo="requests",
        snapshot_sha=_SNAP,
        head_sha="H" * 40,
        window_days=365,
        n_functions=len(snapshot.report.functions),
        n_fixes_scanned=5,
        n_fixes_blamed=4,
        n_implications_untracked=1,
        counts={target: 2},
    )


def test_defects_then_predict_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(harness, "DEFECT_LABELS_PATH", tmp_path / "defect-labels.json")
    monkeypatch.setattr(harness, "DEFECT_PREDICTION_PATH", tmp_path / "defect-prediction.json")
    monkeypatch.setattr(coverage_replay, "CACHE_DIR", tmp_path / "_cache")

    snapshot = _snapshot(tmp_path / "proj")
    labels = _labels(snapshot)

    def _fake_collect(repo, **kwargs):  # type: ignore[no-untyped-def]
        return snapshot, labels

    monkeypatch.setattr(harness, "collect_defect_labels", _fake_collect)

    assert harness.main(["defects", "--repos", "requests"]) == 0
    labels_out = json.loads(harness.DEFECT_LABELS_PATH.read_text())
    req = labels_out["repos"]["requests"]
    assert req["snapshot_sha"] == _SNAP
    assert req["n_defect_functions"] == 1
    assert req["labels"][0]["defect_count"] == 2

    # Seed the snapshot analyze cache so predict can reload the scored report.
    cache = coverage_replay.revision_cache_dir("requests", _SNAP)
    cache.mkdir(parents=True)
    (cache / "analyze.json").write_text(json.dumps(report_to_dict(snapshot.report)), encoding="utf-8")

    assert harness.main(["predict"]) == 0
    pred = json.loads(harness.DEFECT_PREDICTION_PATH.read_text())
    repo = pred["repos"]["requests"]
    assert repo["n_buggy"] == 1
    assert repo["n_clean"] == len(snapshot.report.functions) - 1
    keys = {c["candidate"] for c in repo["candidates"]}
    assert "baseline" in keys and "drop_file_line" in keys


def test_predict_without_labels_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(harness, "DEFECT_LABELS_PATH", tmp_path / "nope.json")
    assert harness.main(["predict"]) == 1


def test_labels_dict_round_trip(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    labels = _labels(snapshot)
    restored = harness._labels_from_dict("requests", harness._labels_to_dict(labels))
    assert restored.counts == labels.counts
    assert restored.snapshot_sha == labels.snapshot_sha
    assert restored.n_implications_untracked == 1
