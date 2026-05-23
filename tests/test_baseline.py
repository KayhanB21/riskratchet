"""Tests for baseline I/O and regression comparison."""

from __future__ import annotations

from pathlib import Path

import pytest

from riskratchet.baseline import (
    baseline_from_report,
    compare,
    load_baseline,
    save_baseline,
)
from riskratchet.models import (
    Baseline,
    BaselineEntry,
    ChurnStats,
    ComplexityStats,
    CoverageStats,
    FileStats,
    FunctionId,
    FunctionRisk,
    FunctionSpan,
    RegressionKind,
    RiskComponents,
    RiskReport,
)


def _components(score: float = 50.0) -> RiskComponents:
    return RiskComponents(
        coverage_gap=score,
        structural_complexity=score,
        branch_gap=score,
        churn=score,
        public_surface=score,
        sprawl=score,
    )


def _fn(path: str, qualname: str, score: float = 50.0) -> FunctionRisk:
    file_stats = FileStats(path=path, total_lines=100, function_count=1)
    return FunctionRisk(
        id=FunctionId(path=path, qualname=qualname),
        span=FunctionSpan(start_line=1, end_line=10),
        is_public=True,
        complexity=ComplexityStats(cyclomatic=5),
        coverage=CoverageStats(line_coverage=0.5, branch_coverage=0.5),
        churn=ChurnStats(commits=0),
        file_stats=file_stats,
        components=_components(score),
        score=score,
        crap=10.0,
    )


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    report = RiskReport(
        functions=(_fn("a.py", "foo", 42.0), _fn("b.py", "bar", 60.0)),
        files=(),
    )
    baseline = baseline_from_report(report)
    target = tmp_path / "baseline.json"
    save_baseline(baseline, target)
    loaded = load_baseline(target)
    assert set(loaded.entries.keys()) == {
        FunctionId("a.py", "foo"),
        FunctionId("b.py", "bar"),
    }
    assert loaded.entries[FunctionId("a.py", "foo")].score == pytest.approx(42.0)


def test_compare_flags_new_function_above_threshold() -> None:
    report = RiskReport(functions=(_fn("a.py", "foo", 60.0),), files=())
    old = Baseline(version="1", entries={})
    regressions = compare(report, old, fail_new_above=50.0, fail_regression_above=5.0)
    assert len(regressions) == 1
    assert regressions[0].kind == RegressionKind.NEW_ABOVE_THRESHOLD


def test_compare_ignores_new_function_below_threshold() -> None:
    report = RiskReport(functions=(_fn("a.py", "foo", 40.0),), files=())
    old = Baseline(version="1", entries={})
    assert compare(report, old, fail_new_above=50.0, fail_regression_above=5.0) == []


def test_compare_flags_regression_beyond_tolerance() -> None:
    fn = _fn("a.py", "foo", 60.0)
    old_entry = BaselineEntry(id=fn.id, score=50.0, components=_components(50.0))
    old = Baseline(version="1", entries={fn.id: old_entry})
    report = RiskReport(functions=(fn,), files=())
    regressions = compare(report, old, fail_new_above=100.0, fail_regression_above=5.0)
    assert len(regressions) == 1
    assert regressions[0].kind == RegressionKind.REGRESSED
    assert regressions[0].delta == pytest.approx(10.0)


def test_compare_ignores_regression_at_tolerance_boundary() -> None:
    fn = _fn("a.py", "foo", 55.0)
    old_entry = BaselineEntry(id=fn.id, score=50.0, components=_components(50.0))
    old = Baseline(version="1", entries={fn.id: old_entry})
    report = RiskReport(functions=(fn,), files=())
    # delta of exactly 5.0 should NOT trigger; tolerance is `>`, not `>=`.
    regressions = compare(report, old, fail_new_above=100.0, fail_regression_above=5.0)
    assert regressions == []


def test_compare_ignores_improvements() -> None:
    fn = _fn("a.py", "foo", 30.0)
    old_entry = BaselineEntry(id=fn.id, score=80.0, components=_components(80.0))
    old = Baseline(version="1", entries={fn.id: old_entry})
    report = RiskReport(functions=(fn,), files=())
    assert compare(report, old, fail_new_above=100.0, fail_regression_above=5.0) == []


def test_compare_ignores_deleted_functions() -> None:
    deleted_id = FunctionId("a.py", "gone")
    old = Baseline(
        version="1",
        entries={deleted_id: BaselineEntry(id=deleted_id, score=80.0, components=_components(80.0))},
    )
    report = RiskReport(functions=(), files=())
    assert compare(report, old, fail_new_above=100.0, fail_regression_above=5.0) == []


def test_baseline_json_is_sorted_for_stable_diffs(tmp_path: Path) -> None:
    report = RiskReport(
        functions=(
            _fn("z.py", "foo", 10.0),
            _fn("a.py", "bar", 10.0),
            _fn("a.py", "foo", 10.0),
        ),
        files=(),
    )
    target = tmp_path / "baseline.json"
    save_baseline(baseline_from_report(report), target)
    text = target.read_text(encoding="utf-8")
    a_foo = text.index("a.py")
    z_foo = text.index("z.py")
    assert a_foo < z_foo
