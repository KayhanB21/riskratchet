"""Full baseline comparison, including non-failing statuses.

`diff` returns every function's relationship to the baseline —
regressed, component-regressed, ambiguous-rename, new, improved, moved,
removed, unchanged — for the `diff` command and the PR-comment body.
The failing subset is projected back into `Regression` objects by the
`regressions` module.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from riskratchet.baseline.classify import (
    _classify_against_baseline,
    _component_regression,
    _current_fingerprint_counts,
    _unique_old_entries_by_fingerprint,
)
from riskratchet.groups import group_for_path
from riskratchet.matching import MatchResult
from riskratchet.models import (
    Baseline,
    BaselineEntry,
    DiffEntry,
    DiffReport,
    DiffStatus,
    FunctionId,
    FunctionRisk,
    RiskReport,
)


def diff(
    new: RiskReport,
    old: Baseline,
    *,
    fail_regression_above: float,
    fail_component_regression_above: float = 15.0,
    component_regression_gate: bool = True,
    groups: Mapping[str, Sequence[str]] | None = None,
) -> DiffReport:
    """Return a full baseline comparison, including non-failing statuses."""
    entries: list[DiffEntry] = []
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
                entries.append(_ambiguous_diff_entry(fn, classification.ambiguous))
            else:
                entries.append(_new_diff_entry(fn))
            continue

        used_old_ids.add(previous.id)
        delta = fn.score - previous.score
        previous_id = classification.previous_id
        status = _diff_status_for_existing(
            fn,
            previous,
            delta=delta,
            fail_regression_above=fail_regression_above,
            fail_component_regression_above=fail_component_regression_above,
            component_regression_gate=component_regression_gate,
            moved=previous_id is not None,
        )
        entries.append(
            DiffEntry(
                id=fn.id,
                status=status,
                current_score=fn.score,
                previous_score=previous.score,
                delta=delta,
                current=fn,
                previous=previous,
                previous_id=previous_id,
                group=fn.group or _group_for_baseline_entry(previous, groups),
                reason=_diff_reason(
                    fn,
                    previous,
                    status=status,
                    delta=delta,
                    previous_id=previous_id,
                    fail_regression_above=fail_regression_above,
                    fail_component_regression_above=fail_component_regression_above,
                ),
                match_confidence=classification.match_confidence,
            )
        )

    current_ids = {fn.id for fn in new.functions}
    for previous in old.entries.values():
        if previous.id in used_old_ids or previous.id in current_ids:
            continue
        entries.append(
            DiffEntry(
                id=previous.id,
                status=DiffStatus.REMOVED,
                current_score=None,
                previous_score=previous.score,
                delta=None,
                previous=previous,
                group=_group_for_baseline_entry(previous, groups),
                reason=f"removed function from baseline with score {previous.score:.1f}",
            )
        )

    entries.sort(key=_diff_sort_key)
    return DiffReport(entries=tuple(entries))


def _diff_status_for_existing(
    fn: FunctionRisk,
    previous: BaselineEntry,
    *,
    delta: float,
    fail_regression_above: float,
    fail_component_regression_above: float,
    component_regression_gate: bool,
    moved: bool,
) -> DiffStatus:
    if delta > fail_regression_above:
        return DiffStatus.REGRESSED
    if delta < -fail_regression_above:
        return DiffStatus.IMPROVED
    if component_regression_gate and _component_regression(
        fn.components,
        previous.components,
        tolerance=fail_component_regression_above,
    ):
        return DiffStatus.COMPONENT_REGRESSED
    if moved:
        return DiffStatus.MOVED
    return DiffStatus.UNCHANGED


def _diff_reason(
    fn: FunctionRisk,
    previous: BaselineEntry,
    *,
    status: DiffStatus,
    delta: float,
    previous_id: FunctionId | None,
    fail_regression_above: float,
    fail_component_regression_above: float,
) -> str:
    previous_note = f" after matching previous target {previous_id.as_target()}" if previous_id else ""
    if status is DiffStatus.REGRESSED:
        return (
            f"risk grew by {delta:+.1f}{previous_note} "
            f"(from {previous.score:.1f} to {fn.score:.1f}); "
            f"tolerance is {fail_regression_above:+.1f}"
        )
    if status is DiffStatus.IMPROVED:
        return f"risk improved by {delta:+.1f} (from {previous.score:.1f} to {fn.score:.1f})"
    if status is DiffStatus.COMPONENT_REGRESSED:
        component_regression = _component_regression(
            fn.components,
            previous.components,
            tolerance=fail_component_regression_above,
        )
        if component_regression is None:
            return "component regression"
        name, previous_value, current_value, component_delta = component_regression
        return (
            f"{name} grew by {component_delta:+.1f} "
            f"(from {previous_value:.1f} to {current_value:.1f}); "
            f"component tolerance is {fail_component_regression_above:+.1f}"
        )
    if status is DiffStatus.MOVED and previous_id is not None:
        return f"moved from {previous_id.as_target()} with no score regression"
    return f"risk unchanged at {fn.score:.1f}"


def _diff_sort_key(entry: DiffEntry) -> tuple[int, float, str]:
    order = {
        DiffStatus.REGRESSED: 0,
        DiffStatus.COMPONENT_REGRESSED: 1,
        DiffStatus.AMBIGUOUS_RENAME: 2,
        DiffStatus.NEW: 3,
        DiffStatus.IMPROVED: 4,
        DiffStatus.MOVED: 5,
        DiffStatus.REMOVED: 6,
        DiffStatus.UNCHANGED: 7,
    }
    magnitude = abs(entry.delta or entry.current_score or entry.previous_score or 0.0)
    return (order[entry.status], -magnitude, entry.id.as_target())


def _group_for_baseline_entry(
    entry: BaselineEntry,
    groups: Mapping[str, Sequence[str]] | None,
) -> str | None:
    if entry.group is not None:
        return entry.group
    if groups is None:
        return None
    return group_for_path(entry.id.path, groups)


def _ambiguous_diff_entry(fn: FunctionRisk, ambiguous: MatchResult) -> DiffEntry:
    targets = ", ".join(c.id.as_target() for c in ambiguous.candidates)
    return DiffEntry(
        id=fn.id,
        status=DiffStatus.AMBIGUOUS_RENAME,
        current_score=fn.score,
        previous_score=None,
        delta=None,
        current=fn,
        group=fn.group,
        reason=(
            f"ambiguous rename candidate (confidence {ambiguous.confidence:.2f}); "
            f"current score {fn.score:.1f} could match: {targets}"
        ),
        previous_targets=tuple(c.id for c in ambiguous.candidates),
        match_confidence=ambiguous.confidence,
    )


def _new_diff_entry(fn: FunctionRisk) -> DiffEntry:
    return DiffEntry(
        id=fn.id,
        status=DiffStatus.NEW,
        current_score=fn.score,
        previous_score=None,
        delta=None,
        current=fn,
        group=fn.group,
        reason=f"function is absent from baseline with score {fn.score:.1f}",
    )
