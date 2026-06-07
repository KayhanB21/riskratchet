"""Tests for tracking an implicated function back to the snapshot population."""

from __future__ import annotations

from pathlib import Path

from bin.calibration.corpus import analyze_report
from bin.calibration.defects import SnapshotPopulation, build_labels, track_to_snapshot
from bin.calibration.szz import Implication

from riskratchet.analysis import DiscoveredFunction, ParsedFile, parse_file
from riskratchet.models import FunctionId

_PARSE = (
    "def parse(text):\n    value = text.strip()\n    if not value:\n        return None\n    return value\n"
)
_OTHER = "def compute(n):\n    total = 0\n    for i in range(n):\n        total += i * i\n    return total\n"


def _discovered(tmp: Path, rel: str, body: str) -> DiscoveredFunction:
    target = tmp / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    parsed = parse_file(target, root=tmp)
    assert isinstance(parsed, ParsedFile)
    return parsed.functions[0]


def _snapshot(tmp: Path, files: dict[str, str]) -> SnapshotPopulation:
    root = tmp / "snap"
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    report = analyze_report([root], root)
    return SnapshotPopulation(snapshot_sha="S" * 40, report=report)


def _impl(fn: DiscoveredFunction) -> Implication:
    return Implication(fix_sha="f" * 40, introducer_sha="a" * 40, parent_fn_id=fn.id, parent_fn=fn)


def test_exact_id_hit(tmp_path: Path) -> None:
    snap = _snapshot(tmp_path, {"m.py": _PARSE})
    parent = _discovered(tmp_path / "parent", "m.py", _PARSE)
    assert track_to_snapshot(_impl(parent), snap) == FunctionId("m.py", "parse")


def test_moved_function_fingerprint_hit(tmp_path: Path) -> None:
    # Snapshot has `parse` at new.py; the fix-parent had identical `parse` at old.py.
    snap = _snapshot(tmp_path, {"new.py": _PARSE})
    parent = _discovered(tmp_path / "parent", "old.py", _PARSE)
    tracked = track_to_snapshot(_impl(parent), snap)
    assert tracked == FunctionId("new.py", "parse")


def test_refactored_miss(tmp_path: Path) -> None:
    # Different id, different body, different qualname tail => no candidates, no match.
    snap = _snapshot(tmp_path, {"m.py": _OTHER})  # only `compute` exists at S
    parent = _discovered(tmp_path / "parent", "legacy.py", _PARSE)  # `parse`, unrelated
    assert track_to_snapshot(_impl(parent), snap) is None


def test_build_labels_counts_distinct_fixes(tmp_path: Path) -> None:
    snap = _snapshot(tmp_path, {"m.py": _PARSE})
    parent = _discovered(tmp_path / "parent", "m.py", _PARSE)
    impls = [
        Implication("fix1" + "0" * 36, "a" * 40, parent.id, parent),
        Implication("fix1" + "0" * 36, "a" * 40, parent.id, parent),  # same fix, dup line
        Implication("fix2" + "0" * 36, "b" * 40, parent.id, parent),  # distinct fix
    ]
    labels = build_labels(
        "demo", snap, impls, head_sha="H" * 40, window_days=365, n_fixes_scanned=2, n_fixes_blamed=2
    )
    assert labels.counts[FunctionId("m.py", "parse")] == 2  # two DISTINCT fixes
    assert labels.n_defect_functions == 1
    assert labels.n_implications_untracked == 0


def test_build_labels_records_untracked(tmp_path: Path) -> None:
    snap = _snapshot(tmp_path, {"m.py": _OTHER})
    parent = _discovered(tmp_path / "parent", "legacy.py", _PARSE)
    labels = build_labels(
        "demo", snap, [_impl(parent)], head_sha="H" * 40, window_days=365, n_fixes_scanned=1, n_fixes_blamed=1
    )
    assert labels.counts == {}
    assert labels.n_implications_untracked == 1
