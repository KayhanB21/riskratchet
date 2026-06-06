"""Tests for the defect-prediction AUC analysis."""

from __future__ import annotations

import math
from pathlib import Path

from bin.calibration.corpus import analyze_report
from bin.calibration.defects import DefectLabels, SnapshotPopulation
from bin.calibration.predict import auc_from_mwu, evaluate_candidates
from bin.calibration.rescore import CANDIDATES

from riskratchet.models import FunctionId


def test_auc_perfect_and_reversed_and_empty() -> None:
    assert auc_from_mwu([10.0, 11.0, 12.0], [1.0, 2.0, 3.0]) == 1.0  # buggy always higher
    assert auc_from_mwu([1.0, 2.0, 3.0], [10.0, 11.0, 12.0]) == 0.0  # buggy always lower
    assert round(auc_from_mwu([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]), 4) == 0.5  # no separation
    assert math.isnan(auc_from_mwu([], [1.0, 2.0]))


def _populated_snapshot(tmp_path: Path) -> tuple[SnapshotPopulation, DefectLabels]:
    # Build a project where the "buggy" function lives in a huge file (high sprawl)
    # and the "clean" ones live in small files. Then label the big-file function
    # defective, so a sprawl-sensitive score should separate them.
    root = tmp_path / "snap"
    src = root / "src"
    src.mkdir(parents=True)
    body = "def f{n}(x):\n    return x + {n}\n"
    # clean small files
    for i in range(6):
        (src / f"small{i}.py").write_text(body.format(n=i), encoding="utf-8")
    # buggy function padded into a >1000-line file (file-line sprawl saturates)
    pad = "".join(f"# pad {j}\n" for j in range(1300))
    (src / "big.py").write_text(body.format(n=99) + pad, encoding="utf-8")

    report = analyze_report([src], root)
    snapshot = SnapshotPopulation(snapshot_sha="S" * 40, report=report)
    labels = DefectLabels(
        repo="demo",
        snapshot_sha="S" * 40,
        head_sha="H" * 40,
        window_days=365,
        n_functions=len(report.functions),
        n_fixes_scanned=1,
        n_fixes_blamed=1,
        n_implications_untracked=0,
        counts={FunctionId("src/big.py", "f99"): 1},
    )
    return snapshot, labels


def test_evaluate_candidates_shape_and_sprawl_separation(tmp_path: Path) -> None:
    snapshot, labels = _populated_snapshot(tmp_path)
    results = evaluate_candidates(snapshot, labels)

    # One row per candidate, in CANDIDATES order.
    assert [r.candidate for r in results] == [c.key for c in CANDIDATES]

    by_key = {r.candidate: r for r in results}
    # The lone defect function sits in the big file, so under baseline its sprawl
    # is the maximum in the population => sprawl AUC is a perfect 1.0.
    assert by_key["baseline"].sprawl_auc == 1.0
    # Dropping the file-line term collapses that signal (the function body is
    # tiny), so the sprawl AUC must fall.
    assert by_key["drop_file_line"].sprawl_auc < by_key["baseline"].sprawl_auc
