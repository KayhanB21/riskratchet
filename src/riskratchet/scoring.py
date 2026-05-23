"""Pure scoring functions for risk and the classic CRAP score.

Every function here is deterministic and side-effect-free. The risk score is a
weighted sum of six normalized component scores in the range [0, 100]; the
weights and saturation thresholds live in module-level constants so callers
and tests can introspect them.
"""

from __future__ import annotations

from riskratchet.models import (
    ChurnStats,
    ComplexityStats,
    CoverageStats,
    FileStats,
    FunctionSpan,
    RiskComponents,
    Severity,
)

# Weights for the six risk components. They sum to 1.0 so the total risk score
# stays bounded in [0, 100] when each component is also in [0, 100].
WEIGHTS: dict[str, float] = {
    "coverage_gap": 0.30,
    "structural_complexity": 0.25,
    "branch_gap": 0.15,
    "churn": 0.10,
    "public_surface": 0.10,
    "sprawl": 0.10,
}

# Saturation thresholds. A value at or above the saturation point scores 100.
COMPLEXITY_SATURATION_CC = 20
CHURN_SATURATION_COMMITS = 10
FUNCTION_LINE_FREE = 80
FUNCTION_LINE_SATURATION = 160
FILE_LINE_FREE = 500
FILE_LINE_SATURATION = 1000

# Severity bands. Boundaries are inclusive at the lower bound.
SEVERITY_BANDS: tuple[tuple[float, Severity], ...] = (
    (75.0, Severity.CRITICAL),
    (50.0, Severity.HIGH),
    (25.0, Severity.MEDIUM),
    (0.0, Severity.LOW),
)


def _saturate(value: float, free: float, saturation: float) -> float:
    """Map `value` to [0, 100] starting from `free` and saturating at `saturation`."""
    if saturation <= free:
        raise ValueError("saturation must be greater than free")
    if value <= free:
        return 0.0
    if value >= saturation:
        return 100.0
    return (value - free) / (saturation - free) * 100.0


def coverage_gap_score(coverage: CoverageStats) -> float:
    return max(0.0, min(1.0, 1.0 - coverage.line_coverage)) * 100.0


def structural_complexity_score(complexity: ComplexityStats) -> float:
    return _saturate(complexity.cyclomatic, free=1, saturation=COMPLEXITY_SATURATION_CC + 1)


def branch_gap_score(coverage: CoverageStats) -> float:
    if coverage.branch_coverage is None:
        return 0.0
    return max(0.0, min(1.0, 1.0 - coverage.branch_coverage)) * 100.0


def churn_score(churn: ChurnStats) -> float:
    return _saturate(churn.commits, free=0, saturation=CHURN_SATURATION_COMMITS)


def public_surface_score(is_public: bool, coverage: CoverageStats) -> float:
    if not is_public:
        return 0.0
    return max(0.0, min(1.0, 1.0 - coverage.line_coverage)) * 100.0


def sprawl_score(span: FunctionSpan, file_stats: FileStats) -> float:
    function_score = _saturate(
        span.line_count,
        free=FUNCTION_LINE_FREE,
        saturation=FUNCTION_LINE_SATURATION,
    )
    file_score = _saturate(
        file_stats.total_lines,
        free=FILE_LINE_FREE,
        saturation=FILE_LINE_SATURATION,
    )
    return (function_score + file_score) / 2.0


def total_risk(components: RiskComponents) -> float:
    raw = (
        WEIGHTS["coverage_gap"] * components.coverage_gap
        + WEIGHTS["structural_complexity"] * components.structural_complexity
        + WEIGHTS["branch_gap"] * components.branch_gap
        + WEIGHTS["churn"] * components.churn
        + WEIGHTS["public_surface"] * components.public_surface
        + WEIGHTS["sprawl"] * components.sprawl
    )
    return max(0.0, min(100.0, raw))


def crap_score(complexity: ComplexityStats, coverage: CoverageStats) -> float:
    """Classic CRAP: CC^2 * (1 - line_coverage)^3 + CC."""
    cc = complexity.cyclomatic
    gap = max(0.0, min(1.0, 1.0 - coverage.line_coverage))
    return cc * cc * (gap**3) + cc


def severity(score: float) -> Severity:
    for threshold, level in SEVERITY_BANDS:
        if score >= threshold:
            return level
    return Severity.LOW


def compute_components(
    *,
    is_public: bool,
    span: FunctionSpan,
    complexity: ComplexityStats,
    coverage: CoverageStats,
    churn: ChurnStats,
    file_stats: FileStats,
) -> RiskComponents:
    return RiskComponents(
        coverage_gap=coverage_gap_score(coverage),
        structural_complexity=structural_complexity_score(complexity),
        branch_gap=branch_gap_score(coverage),
        churn=churn_score(churn),
        public_surface=public_surface_score(is_public, coverage),
        sprawl=sprawl_score(span, file_stats),
    )
