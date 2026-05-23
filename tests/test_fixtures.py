"""Golden-fixture regression tests.

Each fixture under `tests/fixtures/<name>/` represents a canonical risk
shape. These tests pin down "given this canonical shape, riskratchet
should say this" so a refactor of scoring cannot quietly change the
verdict on a known case.

They also double as documentation: read the fixture source to see what
each risk shape looks like in code, then read the assertion to see what
the tool catches.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from riskratchet.baseline import compare, load_baseline
from riskratchet.engine import analyze
from riskratchet.models import RiskReport
from riskratchet.scoring import severity as severity_of

FIXTURE_ROOT = Path(__file__).parent / "fixtures"


def _analyze_fixture(name: str) -> RiskReport:
    fixture_dir = FIXTURE_ROOT / name
    return analyze(
        [fixture_dir / "src"],
        root=fixture_dir,
        coverage_path=fixture_dir / "coverage.json",
        use_git=False,
    )


def _by_qualname(report: RiskReport) -> dict[str, float]:
    return {fn.id.qualname: fn.score for fn in report.functions}


def test_simple_clean_has_no_risk() -> None:
    report = _analyze_fixture("simple_clean")
    scores = _by_qualname(report)
    assert set(scores.keys()) == {"add", "identity"}
    for score in scores.values():
        assert severity_of(score).value == "low"


def test_complex_uncovered_flags_classify_as_critical() -> None:
    report = _analyze_fixture("complex_uncovered")
    fn = next(fn for fn in report.functions if fn.id.qualname == "classify")
    assert fn.complexity.cyclomatic >= 10
    assert fn.coverage.line_coverage == 0.0
    assert fn.components.coverage_gap == pytest.approx(100.0)
    assert fn.components.structural_complexity >= 50.0
    assert severity_of(fn.score).value in {"high", "critical"}


def test_covered_but_branchy_fires_branch_gap_only() -> None:
    report = _analyze_fixture("covered_but_branchy")
    fn = next(fn for fn in report.functions if fn.id.qualname == "normalize")
    assert fn.coverage.line_coverage == 1.0
    assert fn.components.coverage_gap == 0.0
    assert fn.coverage.branch_coverage is not None
    assert fn.components.branch_gap > 0.0


def test_large_file_sprawl_pushes_sprawl_component() -> None:
    report = _analyze_fixture("large_file_sprawl")
    fn = next(fn for fn in report.functions if fn.id.qualname == "big_function")
    assert fn.span.line_count >= 80
    assert fn.file_stats.total_lines >= 500
    assert fn.components.sprawl > 0.0


def test_public_api_regression_triggers_check() -> None:
    fixture_dir = FIXTURE_ROOT / "public_api_regression"
    report = analyze(
        [fixture_dir / "src"],
        root=fixture_dir,
        coverage_path=fixture_dir / "coverage.json",
        use_git=False,
    )
    public_fn = next(fn for fn in report.functions if fn.id.qualname == "public_api")
    assert public_fn.is_public
    assert public_fn.components.public_surface > 0.0

    baseline = load_baseline(fixture_dir / "baseline.json")
    regressions = compare(report, baseline, fail_new_above=100.0, fail_regression_above=5.0)
    flagged = {reg.id.qualname for reg in regressions}
    assert "public_api" in flagged
    assert "_private_helper" not in flagged


def test_agent_generated_spaghetti_lands_high() -> None:
    report = _analyze_fixture("agent_generated_spaghetti")
    fn = next(fn for fn in report.functions if fn.id.qualname == "process_payment")
    assert fn.complexity.cyclomatic >= 10
    assert fn.is_public
    assert fn.components.coverage_gap > 0.0
    assert fn.components.structural_complexity > 0.0
    assert severity_of(fn.score).value in {"high", "critical"}
