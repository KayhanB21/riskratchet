"""Tests for the pure scoring functions."""

from __future__ import annotations

import math

import pytest

from riskratchet.models import (
    ChurnStats,
    ComplexityStats,
    CoverageStats,
    FileStats,
    FunctionSpan,
    RiskComponents,
    Severity,
)
from riskratchet.scoring import (
    WEIGHTS,
    branch_gap_score,
    churn_score,
    compute_components,
    coverage_gap_score,
    crap_score,
    public_surface_score,
    severity,
    sprawl_score,
    structural_complexity_score,
    total_risk,
)


def _file(total_lines: int = 100) -> FileStats:
    return FileStats(path="x.py", total_lines=total_lines, function_count=1)


def _span(lines: int = 10) -> FunctionSpan:
    return FunctionSpan(start_line=1, end_line=lines)


def test_weights_sum_to_one() -> None:
    assert math.isclose(sum(WEIGHTS.values()), 1.0)


def test_coverage_gap_score_endpoints() -> None:
    assert coverage_gap_score(CoverageStats(line_coverage=1.0, branch_coverage=None)) == 0.0
    assert coverage_gap_score(CoverageStats(line_coverage=0.0, branch_coverage=None)) == 100.0
    assert coverage_gap_score(CoverageStats(line_coverage=0.5, branch_coverage=None)) == 50.0


def test_branch_gap_score_handles_missing_branch_coverage() -> None:
    assert branch_gap_score(CoverageStats(line_coverage=0.0, branch_coverage=None)) == 0.0
    assert branch_gap_score(CoverageStats(line_coverage=0.0, branch_coverage=0.5)) == 50.0


def test_structural_complexity_saturates_at_cc_21() -> None:
    assert structural_complexity_score(ComplexityStats(cyclomatic=1)) == 0.0
    assert structural_complexity_score(ComplexityStats(cyclomatic=21)) == 100.0
    mid = structural_complexity_score(ComplexityStats(cyclomatic=11))
    assert 49.0 < mid < 51.0


def test_structural_complexity_is_monotonic() -> None:
    values = [structural_complexity_score(ComplexityStats(cyclomatic=cc)) for cc in range(1, 25)]
    assert values == sorted(values)


def test_churn_score_saturates() -> None:
    assert churn_score(ChurnStats(commits=0)) == 0.0
    assert churn_score(ChurnStats(commits=10)) == 100.0
    assert churn_score(ChurnStats(commits=100)) == 100.0


def test_public_surface_score_only_penalises_public_functions() -> None:
    cov = CoverageStats(line_coverage=0.0, branch_coverage=None)
    assert public_surface_score(is_public=False, coverage=cov) == 0.0
    assert public_surface_score(is_public=True, coverage=cov) == 100.0
    well_tested = CoverageStats(line_coverage=1.0, branch_coverage=None)
    assert public_surface_score(is_public=True, coverage=well_tested) == 0.0


def test_sprawl_score_combines_function_and_file_length() -> None:
    small = sprawl_score(_span(20), _file(100))
    big = sprawl_score(_span(160), _file(1000))
    assert small == 0.0
    assert big == 100.0


def test_total_risk_is_bounded_and_weighted() -> None:
    components = RiskComponents(
        coverage_gap=100.0,
        structural_complexity=100.0,
        branch_gap=100.0,
        churn=100.0,
        public_surface=100.0,
        sprawl=100.0,
    )
    assert total_risk(components) == pytest.approx(100.0)

    zero = RiskComponents(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert total_risk(zero) == 0.0


def test_total_risk_matches_weighted_sum() -> None:
    components = RiskComponents(
        coverage_gap=50.0,
        structural_complexity=80.0,
        branch_gap=40.0,
        churn=30.0,
        public_surface=20.0,
        sprawl=10.0,
    )
    expected = (
        0.30 * 50.0
        + 0.25 * 80.0
        + 0.15 * 40.0
        + 0.10 * 30.0
        + 0.10 * 20.0
        + 0.10 * 10.0
    )
    assert total_risk(components) == pytest.approx(expected)


def test_crap_known_values() -> None:
    # CC=10, no coverage: CC^2 * 1^3 + CC = 110
    assert crap_score(
        ComplexityStats(cyclomatic=10),
        CoverageStats(line_coverage=0.0, branch_coverage=None),
    ) == pytest.approx(110.0)
    # CC=10, full coverage: 0 + 10
    assert crap_score(
        ComplexityStats(cyclomatic=10),
        CoverageStats(line_coverage=1.0, branch_coverage=None),
    ) == pytest.approx(10.0)
    # CC=5, 80% coverage: 25 * 0.008 + 5 = 5.2
    assert crap_score(
        ComplexityStats(cyclomatic=5),
        CoverageStats(line_coverage=0.8, branch_coverage=None),
    ) == pytest.approx(5.2)


def test_severity_bands() -> None:
    assert severity(0.0) == Severity.LOW
    assert severity(24.99) == Severity.LOW
    assert severity(25.0) == Severity.MEDIUM
    assert severity(49.99) == Severity.MEDIUM
    assert severity(50.0) == Severity.HIGH
    assert severity(74.99) == Severity.HIGH
    assert severity(75.0) == Severity.CRITICAL
    assert severity(100.0) == Severity.CRITICAL


def test_compute_components_dispatches_correctly() -> None:
    cov = CoverageStats(line_coverage=0.5, branch_coverage=0.5)
    components = compute_components(
        is_public=True,
        span=_span(10),
        complexity=ComplexityStats(cyclomatic=5),
        coverage=cov,
        churn=ChurnStats(commits=3),
        file_stats=_file(200),
    )
    assert components.coverage_gap == 50.0
    assert components.branch_gap == 50.0
    assert components.public_surface == 50.0
    assert 19.0 < components.structural_complexity < 21.0
    assert components.churn == 30.0
    assert components.sprawl == 0.0


def test_increasing_coverage_never_increases_coverage_gap() -> None:
    previous = 100.0
    for cov_percent in range(0, 101, 5):
        cov = CoverageStats(line_coverage=cov_percent / 100.0, branch_coverage=None)
        current = coverage_gap_score(cov)
        assert current <= previous + 1e-9
        previous = current
