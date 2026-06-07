"""Change-proneness label building + serialization (no git/clone needed)."""

from __future__ import annotations

from pathlib import Path

from bin.calibration.corpus import analyze_report
from bin.calibration.defects import SnapshotPopulation
from bin.calibration.proneness import (
    build_proneness_labels,
    labels_from_dict,
    labels_to_dict,
)

from riskratchet.models import FunctionId


def fid(i: int) -> FunctionId:
    return FunctionId("src/m.py", f"f{i}")


def _snapshot(tmp_path: Path, n: int) -> SnapshotPopulation:
    src = tmp_path / "src"
    src.mkdir()
    body = "".join(f"def f{i}(x):\n    return x + {i}\n\n\n" for i in range(n))
    (src / "m.py").write_text(body, encoding="utf-8")
    return SnapshotPopulation(snapshot_sha="S" * 40, report=analyze_report([src], tmp_path))


def test_top_quartile_change_prone(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path, 8)
    future = {fid(7): (5, 10), fid(6): (3, 6), fid(5): (1, 1)}  # three edited, two heavily
    past = {fid(0): (4, 8)}  # an old hotspot that went quiet

    labels = build_proneness_labels(
        "demo",
        snapshot,
        future,
        past,
        head_sha="H" * 40,
        window_days=365,
        n_future_commits=9,
        insufficient_past_history=False,
    )

    assert labels.n_functions == 8
    # ceil(0.25 * 8) = 2 => the two most-edited functions are change-prone.
    assert labels.change_prone == {fid(7), fid(6)}
    assert labels.n_change_prone == 2


def test_zero_activity_is_not_change_prone(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path, 4)
    labels = build_proneness_labels(
        "demo",
        snapshot,
        {},
        {},
        head_sha="H" * 40,
        window_days=365,
        n_future_commits=0,
        insufficient_past_history=True,
    )
    assert labels.change_prone == set()  # nothing edited => nobody is prone


def test_labels_roundtrip(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path, 6)
    future = {fid(5): (3, 7), fid(4): (2, 4)}
    past = {fid(0): (5, 9), fid(5): (1, 2)}
    labels = build_proneness_labels(
        "demo",
        snapshot,
        future,
        past,
        head_sha="H" * 40,
        window_days=365,
        n_future_commits=5,
        insufficient_past_history=False,
    )

    restored = labels_from_dict("demo", labels_to_dict(labels))

    assert restored.future == labels.future
    assert restored.past == labels.past
    assert restored.change_prone == labels.change_prone
    assert restored.n_functions == labels.n_functions
    assert restored.insufficient_past_history is False
