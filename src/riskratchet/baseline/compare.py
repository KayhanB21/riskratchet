"""Regression classification against a baseline (the `check` gate).

`compare` walks the current report against the baseline and returns only
the functions that crossed a configured threshold: new functions above
`fail_new_above`, existing functions whose score grew by more than
`fail_regression_above`, per-component regressions, and existing
functions above `fail_existing_above`. Non-failing statuses are the
`diff` module's job.
"""

from __future__ import annotations

from riskratchet.baseline.classify import (
    _classify_against_baseline,
    _component_regression,
    _current_fingerprint_counts,
    _unique_old_entries_by_fingerprint,
)
from riskratchet.matching import MatchResult
from riskratchet.models import (
    Baseline,
    FunctionId,
    FunctionRisk,
    Regression,
    RegressionKind,
    RiskReport,
)


def compare(
    new: RiskReport,
    old: Baseline,
    *,
    fail_new_above: float,
    fail_regression_above: float,
    fail_existing_above: float | None = None,
    fail_component_regression_above: float = 15.0,
    component_regression_gate: bool = True,
) -> list[Regression]:
    """Return regressions found between `old` (baseline) and `new` (report).

    Existing functions are flagged only when `new.score - old.score >
    fail_regression_above`; the strict comparison preserves the "tolerance is
    the noise floor" semantics from the plan. New functions are flagged only
    when their score is above `fail_new_above`. Ambiguous rename matches are
    treated the same as new functions for gating, with a richer reason so
    the user can decide between the candidates.
    """
    out: list[Regression] = []
    old_by_fingerprint = _unique_old_entries_by_fingerprint(old)
    current_fingerprint_counts = _current_fingerprint_counts(new)
    used_old_ids: set[FunctionId] = {fn.id for fn in new.functions if fn.id in old.entries}

    for fn in new.functions:
        classification = _classify_against_baseline(
            fn, old, old_by_fingerprint, current_fingerprint_counts, used_old_ids
        )
        previous = classification.previous
        if previous is None:
            if classification.ambiguous is not None:
                out.append(_ambiguous_regression(fn, classification.ambiguous))
                continue
            if fn.score > fail_new_above:
                out.append(_new_above_threshold_regression(fn, fail_new_above))
            continue

        used_old_ids.add(previous.id)
        delta = fn.score - previous.score
        if delta > fail_regression_above:
            previous_target = classification.previous_id.as_target() if classification.previous_id else None
            previous_note = f" after matching previous target {previous_target}" if previous_target else ""
            out.append(
                Regression(
                    id=fn.id,
                    kind=RegressionKind.REGRESSED,
                    current_score=fn.score,
                    previous_score=previous.score,
                    delta=delta,
                    reason=(
                        f"risk grew by {delta:+.1f}{previous_note} "
                        f"(from {previous.score:.1f} to {fn.score:.1f}); "
                        f"tolerance is {fail_regression_above:+.1f}"
                    ),
                    current=fn,
                )
            )
            continue

        if component_regression_gate:
            component_regression = _component_regression(
                fn.components,
                previous.components,
                tolerance=fail_component_regression_above,
            )
            if component_regression is not None:
                name, previous_value, current_value, component_delta = component_regression
                out.append(
                    Regression(
                        id=fn.id,
                        kind=RegressionKind.COMPONENT_REGRESSED,
                        current_score=fn.score,
                        previous_score=previous.score,
                        delta=component_delta,
                        reason=(
                            f"{name} grew by {component_delta:+.1f} "
                            f"(from {previous_value:.1f} to {current_value:.1f}); "
                            f"component tolerance is {fail_component_regression_above:+.1f}"
                        ),
                        current=fn,
                    )
                )
                continue

        if fail_existing_above is not None and fn.score > fail_existing_above:
            out.append(
                Regression(
                    id=fn.id,
                    kind=RegressionKind.EXISTING_ABOVE_THRESHOLD,
                    current_score=fn.score,
                    previous_score=previous.score,
                    delta=delta,
                    reason=(
                        f"existing function score {fn.score:.1f} "
                        f"exceeds existing-risk threshold {fail_existing_above:.1f}"
                    ),
                    current=fn,
                )
            )
    out.sort(key=lambda r: (-(r.delta or r.current_score), r.id.as_target()))
    return out


def _ambiguous_regression(fn: FunctionRisk, ambiguous: MatchResult) -> Regression:
    targets = ", ".join(c.id.as_target() for c in ambiguous.candidates)
    return Regression(
        id=fn.id,
        kind=RegressionKind.NEW_ABOVE_THRESHOLD,
        current_score=fn.score,
        previous_score=None,
        delta=None,
        reason=(
            f"ambiguous rename candidate (confidence {ambiguous.confidence:.2f}); "
            f"current score {fn.score:.1f} could match: {targets}. "
            "Resolve by accepting the new baseline or renaming back."
        ),
        current=fn,
    )


def _new_above_threshold_regression(fn: FunctionRisk, fail_new_above: float) -> Regression:
    return Regression(
        id=fn.id,
        kind=RegressionKind.NEW_ABOVE_THRESHOLD,
        current_score=fn.score,
        previous_score=None,
        delta=None,
        reason=(
            f"function is absent from baseline with score {fn.score:.1f}; "
            f"exceeds new-function threshold {fail_new_above:.1f}"
        ),
        current=fn,
    )
