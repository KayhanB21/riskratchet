"""Pure scoring functions for risk and the classic CRAP score.

Every function here is deterministic and side-effect-free. The risk score is a
weighted sum of six normalized component scores in the range [0, 100]; the
weights and saturation thresholds live in module-level constants so callers
and tests can introspect them.
"""

from __future__ import annotations

from collections.abc import Mapping

from riskratchet.models import (
    ChurnStats,
    ComplexityStats,
    CoverageStats,
    FileStats,
    FunctionSpan,
    RiskComponents,
    Severity,
)

# Default weights for the six risk components. They sum to 1.0 so the total
# risk score stays bounded in [0, 100] when each component is also in [0, 100].
# Callers can override individual weights via `[tool.riskratchet.weights]` in
# `pyproject.toml`; `resolve_weights` merges and renormalizes so the bound is
# always preserved.
DEFAULT_WEIGHTS: dict[str, float] = {
    "coverage_gap": 0.30,
    "structural_complexity": 0.25,
    "branch_gap": 0.15,
    "churn": 0.10,
    "public_surface": 0.10,
    "sprawl": 0.10,
}

# Back-compat alias. Prefer `DEFAULT_WEIGHTS` in new code.
WEIGHTS = DEFAULT_WEIGHTS

COMPONENT_NAMES: frozenset[str] = frozenset(DEFAULT_WEIGHTS)

# Saturation thresholds. A value at or above the saturation point scores 100.
COMPLEXITY_SATURATION_CC = 20
CHURN_SATURATION_COMMITS = 10

# Cyclomatic-complexity normalization band as a `(free, saturation)` pair. It is threaded
# through the scoring functions as a plain *value*, never branched on a language string, so
# scoring.py stays language-agnostic: the Python backend passes `PYTHON_COMPLEXITY_CALIBRATION`
# and the TypeScript backend (0.3.0) passes its own corpus-derived band, so equal complexity
# percentiles map to equal normalized scores across languages. `PYTHON_COMPLEXITY_CALIBRATION`
# is exactly today's literal — `structural_complexity_score` was hardcoded to
# `free=1, saturation=COMPLEXITY_SATURATION_CC + 1` — so the default path is byte-identical.
ComplexityCalibration = tuple[float, float]
PYTHON_COMPLEXITY_CALIBRATION: ComplexityCalibration = (1.0, float(COMPLEXITY_SATURATION_CC + 1))

# TypeScript complexity band, DERIVED — not hand-picked — by endpoint-percentile matching against
# the Python band (see `bin/calibration/ts_complexity_calibration.py`,
# `data/calibration/ts-complexity-calibration.json`, and `docs/typescript-complexity-calibration.md`).
# Over a 12-repo, 2,744-function TS corpus vs a 59,226-function Python corpus, the TS cyclomatic
# value at the percentile where Python's saturation=21 falls (~98.85th) is ~21.3 → rounds to 21, and
# the free anchor is 1 in both. So the data says TS needs no different band than Python at 0.3.0: the
# shared (1, 21) is the conservative, evidence-backed outcome, not an assumption. It is a *distinct*
# named constant so a future re-derivation (larger corpus, grammar/rule change) can move TS without
# touching Python. Unconsumed until the TS engine threads it into `compute_components` (B3).
TYPESCRIPT_COMPLEXITY_CALIBRATION: ComplexityCalibration = (1.0, 21.0)
FUNCTION_LINE_FREE = 80
FUNCTION_LINE_SATURATION = 160
# The file-line band no longer feeds scoring (dropped in 0.3.0 — see sprawl_score). Retained
# because the calibration harness (bin/calibration/rescore.py) still references it to compare
# the shipped scoring against the drop/shrink/raise-band candidates.
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


def structural_complexity_score(
    complexity: ComplexityStats,
    calibration: ComplexityCalibration = PYTHON_COMPLEXITY_CALIBRATION,
) -> float:
    free, saturation = calibration
    return _saturate(complexity.cyclomatic, free=free, saturation=saturation)


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
    # 0.3.0 (breaking): the file-line half of sprawl was dropped. Three independent
    # labelled-outcome analyses agree it carries no predictive signal — the 0.2.10 SZZ
    # defect ablation (net-negative, 25/34), the phase-4 change-proneness ablation
    # (net-noise), and the 0.3.0 polished+messy change-proneness gradient (net-noise,
    # coef +0.024, 95% CI spans zero) — and the messy/AI-side-project cohort did not
    # resurface a god-module regime. `sprawl` is now a pure long-function penalty: it
    # fires only for genuinely long functions, no longer rewards cosmetic module splits,
    # and is language-neutral (a per-function line count), so the TypeScript backend
    # inherits it unchanged. See docs/sprawl-component-finding.md "Decision for 0.3.0".
    # `file_stats` is retained in the signature for pipeline/API stability (compute_components
    # threads it uniformly) but is no longer consulted here.
    del file_stats
    return _saturate(
        span.line_count,
        free=FUNCTION_LINE_FREE,
        saturation=FUNCTION_LINE_SATURATION,
    )


class InvalidWeightsError(ValueError):
    """Raised when user-supplied weights cannot be reconciled with the schema."""


def resolve_weights(overrides: Mapping[str, float] | None) -> dict[str, float]:
    """Merge `overrides` onto `DEFAULT_WEIGHTS` and renormalize to sum to 1.0.

    Renormalizing means callers can express "I don't care about churn" by setting
    it to `0`, or "double the weight of coverage" by setting it to `0.6`, without
    having to hand-tune the other five values. Unknown keys and negative values
    are rejected: silently dropping them would let a typo quietly weaken the
    score.
    """
    if not overrides:
        return dict(DEFAULT_WEIGHTS)

    unknown = set(overrides) - COMPONENT_NAMES
    if unknown:
        raise InvalidWeightsError(
            f"unknown weight keys: {sorted(unknown)}. valid keys: {sorted(COMPONENT_NAMES)}"
        )

    merged = dict(DEFAULT_WEIGHTS)
    for name, raw in overrides.items():
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise InvalidWeightsError(f"weight `{name}` must be a number, got {raw!r}") from exc
        if value < 0:
            raise InvalidWeightsError(f"weight `{name}` must be non-negative, got {value}")
        merged[name] = value

    total = sum(merged.values())
    if total <= 0:
        raise InvalidWeightsError("at least one weight must be greater than zero")
    return {name: value / total for name, value in merged.items()}


def total_risk(
    components: RiskComponents,
    *,
    weights: Mapping[str, float] | None = None,
) -> float:
    w = weights if weights is not None else DEFAULT_WEIGHTS
    raw = (
        w["coverage_gap"] * components.coverage_gap
        + w["structural_complexity"] * components.structural_complexity
        + w["branch_gap"] * components.branch_gap
        + w["churn"] * components.churn
        + w["public_surface"] * components.public_surface
        + w["sprawl"] * components.sprawl
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
    complexity_calibration: ComplexityCalibration = PYTHON_COMPLEXITY_CALIBRATION,
) -> RiskComponents:
    return RiskComponents(
        coverage_gap=coverage_gap_score(coverage),
        structural_complexity=structural_complexity_score(complexity, complexity_calibration),
        branch_gap=branch_gap_score(coverage),
        churn=churn_score(churn),
        public_surface=public_surface_score(is_public, coverage),
        sprawl=sprawl_score(span, file_stats),
    )
