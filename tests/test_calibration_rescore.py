"""Tests for candidate sprawl re-scoring and accept/reject separation.

The candidates must be component recomputes, not weight overrides: a regression
driven purely by the file-line sprawl term should vanish under "drop the file-line
term" while the baseline still flags it. These tests pin that math.
"""

from __future__ import annotations

from pathlib import Path

from bin.calibration.corpus import analyze_report
from bin.calibration.rescore import (
    CANDIDATES,
    LabeledPr,
    evaluate,
    regression_count_under,
    rescore_report,
)

# A function whose body never changes between base and head — only the file it
# lives in grows, so any score change is a pure artifact of the file-line term.
_FN = "def f(items):\n    total = 0\n    for it in items:\n        total += it\n    return total\n"


def _candidate(key: str):  # type: ignore[no-untyped-def]
    return next(c for c in CANDIDATES if c.key == key)


def _project(root: Path, pad_lines: int) -> Path:
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    pad = "".join(f"# pad line {i}\n" for i in range(pad_lines))
    (src / "m.py").write_text(_FN + pad, encoding="utf-8")
    return src


def test_file_size_regression_is_suppressed_by_candidates(tmp_path: Path) -> None:
    # Base: small file. Head: identical function, file padded past the 1000-line
    # saturation so the file-line term jumps 0 -> 100.
    base = analyze_report([_project(tmp_path / "base", pad_lines=3)], tmp_path / "base")
    head = analyze_report([_project(tmp_path / "head", pad_lines=1300)], tmp_path / "head")

    # fail_regression_above=4.0: the baseline file-term swing is exactly +5.0.
    baseline_count = regression_count_under(_candidate("baseline"), base, head, fail_regression_above=4.0)
    assert baseline_count == 1

    for key in ("drop_file_line", "shrink_file_share", "raise_band"):
        suppressed = regression_count_under(_candidate(key), base, head, fail_regression_above=4.0)
        assert suppressed == 0, f"{key} should not flag a file-size-only regression"


def test_rescore_only_changes_sprawl_and_score(tmp_path: Path) -> None:
    report = analyze_report([_project(tmp_path, pad_lines=1300)], tmp_path)
    original = report.functions[0]
    rescored = rescore_report(report, _candidate("drop_file_line")).functions[0]
    # Sprawl + total score change; every other component is preserved.
    assert rescored.components.structural_complexity == original.components.structural_complexity
    assert rescored.components.coverage_gap == original.components.coverage_gap
    assert rescored.fingerprint == original.fingerprint
    assert rescored.components.sprawl != original.components.sprawl


def test_evaluate_separates_labels(tmp_path: Path) -> None:
    # One rejected PR with a file-size regression, one accepted PR with none.
    base = analyze_report([_project(tmp_path / "b", pad_lines=3)], tmp_path / "b")
    grown = analyze_report([_project(tmp_path / "g", pad_lines=1300)], tmp_path / "g")
    prs = [
        LabeledPr(repo="r", pr=1, label="rejected", base_report=base, head_report=grown),
        LabeledPr(repo="r", pr=2, label="accepted", base_report=base, head_report=base),
    ]
    results = evaluate(prs, fail_regression_above=4.0)
    by_key = {r["candidate"]: r for r in results}
    # Under baseline, the rejected PR has 1 regression, the accepted has 0.
    assert by_key["baseline"]["rejected_mean"] == 1.0
    assert by_key["baseline"]["accepted_mean"] == 0.0
    assert by_key["baseline"]["n_rejected"] == 1
