"""Project a `DiffReport` down to the failing `Regression` subset.

`diff` produces every status; the gate only cares about the failing
ones. `regressions_from_diff` is the conversion: ambiguous renames and
over-threshold new functions become `NEW_ABOVE_THRESHOLD`, regressed and
component-regressed entries carry through, and existing functions above
`fail_existing_above` are flagged.

`regressions_above_threshold` is the no-baseline gate (`check
--fail-above N`): every current function with `score > N` becomes an
`ABOVE_THRESHOLD` regression. Baseline-less and config-less; the
threshold is the only signal.
"""

from __future__ import annotations

from riskratchet.models import (
    DiffReport,
    DiffStatus,
    Regression,
    RegressionKind,
    RiskReport,
)


def regressions_from_diff(
    report: DiffReport,
    *,
    fail_new_above: float,
    fail_existing_above: float | None = None,
) -> list[Regression]:
    out: list[Regression] = []
    for entry in report.entries:
        if entry.status is DiffStatus.AMBIGUOUS_RENAME:
            current_score = entry.current_score or 0.0
            out.append(
                Regression(
                    id=entry.id,
                    kind=RegressionKind.NEW_ABOVE_THRESHOLD,
                    current_score=current_score,
                    previous_score=None,
                    delta=None,
                    reason=entry.reason
                    or (f"ambiguous rename at score {current_score:.1f}; resolve before accepting baseline."),
                    current=entry.current,
                )
            )
        elif entry.status is DiffStatus.NEW:
            current_score = entry.current_score or 0.0
            if current_score > fail_new_above:
                out.append(
                    Regression(
                        id=entry.id,
                        kind=RegressionKind.NEW_ABOVE_THRESHOLD,
                        current_score=current_score,
                        previous_score=None,
                        delta=None,
                        reason=(
                            f"function is absent from baseline with score {current_score:.1f}; "
                            f"exceeds new-function threshold {fail_new_above:.1f}"
                        ),
                        current=entry.current,
                    )
                )
        elif entry.status is DiffStatus.REGRESSED:
            out.append(
                Regression(
                    id=entry.id,
                    kind=RegressionKind.REGRESSED,
                    current_score=entry.current_score or 0.0,
                    previous_score=entry.previous_score,
                    delta=entry.delta,
                    reason=entry.reason,
                    current=entry.current,
                )
            )
        elif entry.status is DiffStatus.COMPONENT_REGRESSED:
            out.append(
                Regression(
                    id=entry.id,
                    kind=RegressionKind.COMPONENT_REGRESSED,
                    current_score=entry.current_score or 0.0,
                    previous_score=entry.previous_score,
                    delta=entry.delta,
                    reason=entry.reason,
                    current=entry.current,
                )
            )
        elif (
            fail_existing_above is not None
            and entry.current_score is not None
            and entry.status not in {DiffStatus.REMOVED, DiffStatus.NEW, DiffStatus.AMBIGUOUS_RENAME}
            and entry.current_score > fail_existing_above
        ):
            out.append(
                Regression(
                    id=entry.id,
                    kind=RegressionKind.EXISTING_ABOVE_THRESHOLD,
                    current_score=entry.current_score,
                    previous_score=entry.previous_score,
                    delta=entry.delta,
                    reason=(
                        f"existing function score {entry.current_score:.1f} "
                        f"exceeds existing-risk threshold {fail_existing_above:.1f}"
                    ),
                    current=entry.current,
                )
            )
    out.sort(key=lambda r: (-(r.delta or r.current_score), r.id.as_target()))
    return out


def regressions_above_threshold(report: RiskReport, *, threshold: float) -> list[Regression]:
    """Gate a current `RiskReport` against an absolute score threshold.

    No baseline involved: every function whose score strictly exceeds
    `threshold` becomes an `ABOVE_THRESHOLD` regression with
    `previous_score=None` and `delta=None`. Sorted by descending current
    score, ties broken by stable function id.
    """
    out: list[Regression] = []
    for fn in report.functions:
        if fn.score > threshold:
            out.append(
                Regression(
                    id=fn.id,
                    kind=RegressionKind.ABOVE_THRESHOLD,
                    current_score=fn.score,
                    previous_score=None,
                    delta=None,
                    reason=(f"score {fn.score:.1f} exceeds threshold {threshold:.1f} (no baseline)"),
                    current=fn,
                )
            )
    out.sort(key=lambda r: (-r.current_score, r.id.as_target()))
    return out
