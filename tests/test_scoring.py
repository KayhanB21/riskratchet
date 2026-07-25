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
    PYTHON_COMPLEXITY_CALIBRATION,
    TYPESCRIPT_COMPLEXITY_CALIBRATION,
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


def test_structural_complexity_default_calibration_is_python_literal() -> None:
    # B0: the default calibration must reproduce the pre-0.3.0 hardcoded band exactly,
    # so passing PYTHON_COMPLEXITY_CALIBRATION explicitly is byte-identical to omitting it.
    assert PYTHON_COMPLEXITY_CALIBRATION == (1.0, 21.0)
    for cc in range(1, 30):
        stats = ComplexityStats(cyclomatic=cc)
        assert structural_complexity_score(stats) == structural_complexity_score(
            stats, PYTHON_COMPLEXITY_CALIBRATION
        )


def test_typescript_complexity_calibration_is_derived_band() -> None:
    # B2: the TS band is derived by endpoint-percentile matching (see
    # docs/typescript-complexity-calibration.md). It happens to equal Python's (1, 21) on the
    # 0.3.0 corpus, but is a distinct constant so a future re-derivation can move TS alone.
    assert TYPESCRIPT_COMPLEXITY_CALIBRATION == (1.0, 21.0)
    free, saturation = TYPESCRIPT_COMPLEXITY_CALIBRATION
    assert free >= 1.0 and saturation > free  # honors the _saturate precondition


def test_structural_complexity_respects_custom_calibration() -> None:
    # A wider band saturates later: CC=21 is fully saturated under the Python band but only
    # partway up a (1, 41) band. The knob is threaded as a value; no language branching.
    stats = ComplexityStats(cyclomatic=21)
    assert structural_complexity_score(stats, (1.0, 21.0)) == 100.0
    assert structural_complexity_score(stats, (1.0, 41.0)) == pytest.approx(50.0)


def test_compute_components_threads_complexity_calibration() -> None:
    # compute_components must forward the calibration to structural_complexity_score.
    kwargs = dict(
        is_public=True,
        span=_span(10),
        complexity=ComplexityStats(cyclomatic=21),
        coverage=CoverageStats(line_coverage=1.0, branch_coverage=None),
        churn=ChurnStats(commits=0),
        file_stats=_file(100),
    )
    default = compute_components(**kwargs)  # type: ignore[arg-type]
    widened = compute_components(**kwargs, complexity_calibration=(1.0, 41.0))  # type: ignore[arg-type]
    assert default.structural_complexity == 100.0
    assert widened.structural_complexity == pytest.approx(50.0)


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


def test_sprawl_score_is_function_length_only() -> None:
    # 0.3.0: sprawl is the function-length term only; the file-line half was dropped.
    assert sprawl_score(_span(20), _file(100)) == 0.0
    assert sprawl_score(_span(160), _file(1000)) == 100.0
    # File length is irrelevant now: a short function scores 0 and a saturated-length
    # function scores 100 regardless of how big the enclosing file is.
    assert sprawl_score(_span(20), _file(5000)) == 0.0
    assert sprawl_score(_span(160), _file(50)) == 100.0


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
    expected = 0.30 * 50.0 + 0.25 * 80.0 + 0.15 * 40.0 + 0.10 * 30.0 + 0.10 * 20.0 + 0.10 * 10.0
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


# ----- Per-component boundary tests -----


def test_coverage_gap_clamps_out_of_range_inputs() -> None:
    # Defensive bounds: pathological values from upstream coverage tools
    # shouldn't push the component outside [0, 100].
    assert coverage_gap_score(CoverageStats(line_coverage=-0.5, branch_coverage=None)) == 100.0
    assert coverage_gap_score(CoverageStats(line_coverage=1.5, branch_coverage=None)) == 0.0


def test_coverage_gap_near_boundaries() -> None:
    eps = 1e-9
    assert coverage_gap_score(CoverageStats(line_coverage=eps, branch_coverage=None)) == pytest.approx(
        100.0, abs=1e-6
    )
    assert coverage_gap_score(CoverageStats(line_coverage=1.0 - eps, branch_coverage=None)) == pytest.approx(
        0.0, abs=1e-6
    )


def test_structural_complexity_at_saturation_boundary() -> None:
    # CC=1 is the floor (no branches); CC=21 is the first fully-saturated
    # value because _saturate is called with saturation=CC+1=21.
    assert structural_complexity_score(ComplexityStats(cyclomatic=1)) == 0.0
    assert structural_complexity_score(ComplexityStats(cyclomatic=20)) == pytest.approx(95.0)
    assert structural_complexity_score(ComplexityStats(cyclomatic=21)) == 100.0
    assert structural_complexity_score(ComplexityStats(cyclomatic=100)) == 100.0


def test_branch_gap_none_is_zero_regardless_of_line_coverage() -> None:
    for line_cov in (0.0, 0.5, 1.0):
        cov = CoverageStats(line_coverage=line_cov, branch_coverage=None)
        assert branch_gap_score(cov) == 0.0


def test_branch_gap_endpoints_and_mid() -> None:
    assert branch_gap_score(CoverageStats(line_coverage=0.0, branch_coverage=0.0)) == 100.0
    assert branch_gap_score(CoverageStats(line_coverage=0.0, branch_coverage=1.0)) == 0.0
    assert branch_gap_score(CoverageStats(line_coverage=0.0, branch_coverage=0.5)) == 50.0


def test_churn_at_saturation_boundary() -> None:
    assert churn_score(ChurnStats(commits=0)) == 0.0
    assert churn_score(ChurnStats(commits=1)) == pytest.approx(10.0)
    assert churn_score(ChurnStats(commits=9)) == pytest.approx(90.0)
    assert churn_score(ChurnStats(commits=10)) == 100.0
    assert churn_score(ChurnStats(commits=11)) == 100.0
    assert churn_score(ChurnStats(commits=1000)) == 100.0


def test_public_surface_at_coverage_boundaries() -> None:
    private_uncovered = CoverageStats(line_coverage=0.0, branch_coverage=None)
    public_half = CoverageStats(line_coverage=0.5, branch_coverage=None)
    public_full = CoverageStats(line_coverage=1.0, branch_coverage=None)
    assert public_surface_score(is_public=False, coverage=private_uncovered) == 0.0
    assert public_surface_score(is_public=True, coverage=private_uncovered) == 100.0
    assert public_surface_score(is_public=True, coverage=public_half) == 50.0
    assert public_surface_score(is_public=True, coverage=public_full) == 0.0


def test_sprawl_boundaries_track_function_length_only() -> None:
    # 0.3.0: only the function-length band matters; file size never moves sprawl.
    # At/below the function-length free threshold (80): 0, whatever the file size.
    assert sprawl_score(_span(80), _file(500)) == 0.0
    assert sprawl_score(_span(80), _file(1000)) == 0.0
    # At/above the function-length saturation (160): 100, whatever the file size.
    assert sprawl_score(_span(160), _file(1000)) == 100.0
    assert sprawl_score(_span(160), _file(500)) == 100.0
    # Midway up the function-length band, independent of file size.
    assert sprawl_score(_span(120), _file(100)) == pytest.approx(50.0)


def test_severity_bands_at_exact_boundaries() -> None:
    # Lower bound is inclusive at the higher band.
    assert severity(24.999) == Severity.LOW
    assert severity(25.0) == Severity.MEDIUM
    assert severity(49.999) == Severity.MEDIUM
    assert severity(50.0) == Severity.HIGH
    assert severity(74.999) == Severity.HIGH
    assert severity(75.0) == Severity.CRITICAL
