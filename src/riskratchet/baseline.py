"""Baseline JSON I/O and regression detection.

The baseline is the canonical "what we tolerated last time" snapshot. Compare
a fresh `RiskReport` against it and surface only the functions that crossed
the configured thresholds: new functions above `fail_new_above`, existing
functions whose score grew by more than `fail_regression_above`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from riskratchet.groups import group_for_path
from riskratchet.matching import MatchResult, match_rename
from riskratchet.models import (
    Baseline,
    BaselineEntry,
    DiffEntry,
    DiffReport,
    DiffStatus,
    FunctionId,
    FunctionRisk,
    Regression,
    RegressionKind,
    RiskComponents,
    RiskReport,
)

BASELINE_VERSION = "2"


def baseline_from_report(report: RiskReport) -> Baseline:
    entries: dict[FunctionId, BaselineEntry] = {}
    for fn in report.functions:
        entries[fn.id] = BaselineEntry(
            id=fn.id,
            score=round(fn.score, 4),
            components=fn.components,
            fingerprint=fn.fingerprint,
            signature=fn.signature,
            group=fn.group,
        )
    return Baseline(version=BASELINE_VERSION, entries=entries)


def save_baseline(baseline: Baseline, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dumps(baseline), encoding="utf-8")


def load_baseline(path: Path) -> Baseline:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read baseline {path}: {exc}") from exc

    version = str(raw.get("version", BASELINE_VERSION))
    entries: dict[FunctionId, BaselineEntry] = {}
    for raw_entry in raw.get("entries", []):
        entry = _entry_from_dict(raw_entry)
        if entry is not None:
            entries[entry.id] = entry
    return Baseline(version=version, entries=entries)


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


def _dumps(baseline: Baseline) -> str:
    payload: dict[str, Any] = {
        "version": baseline.version,
        "entries": [
            _entry_to_dict(entry)
            for entry in sorted(
                baseline.entries.values(),
                key=lambda e: (e.id.path, e.id.qualname),
            )
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"


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


def _entry_to_dict(entry: BaselineEntry) -> dict[str, Any]:
    c = entry.components
    payload: dict[str, Any] = {
        "path": entry.id.path,
        "qualname": entry.id.qualname,
        "score": round(entry.score, 4),
        "components": {
            "coverage_gap": round(c.coverage_gap, 4),
            "structural_complexity": round(c.structural_complexity, 4),
            "branch_gap": round(c.branch_gap, 4),
            "churn": round(c.churn, 4),
            "public_surface": round(c.public_surface, 4),
            "sprawl": round(c.sprawl, 4),
        },
    }
    if entry.fingerprint is not None:
        payload["fingerprint"] = entry.fingerprint
    if entry.signature is not None:
        payload["signature"] = entry.signature
    if entry.group is not None:
        payload["group"] = entry.group
    return payload


def _entry_from_dict(raw: Any) -> BaselineEntry | None:
    if not isinstance(raw, dict):
        return None
    path = raw.get("path")
    qualname = raw.get("qualname")
    score = raw.get("score")
    components_raw = raw.get("components")
    fingerprint = raw.get("fingerprint")
    signature = raw.get("signature")
    group = raw.get("group")
    if not (
        isinstance(path, str)
        and isinstance(qualname, str)
        and isinstance(score, (int, float))
        and isinstance(components_raw, dict)
    ):
        return None
    components = RiskComponents(
        coverage_gap=float(components_raw.get("coverage_gap", 0.0)),
        structural_complexity=float(components_raw.get("structural_complexity", 0.0)),
        branch_gap=float(components_raw.get("branch_gap", 0.0)),
        churn=float(components_raw.get("churn", 0.0)),
        public_surface=float(components_raw.get("public_surface", 0.0)),
        sprawl=float(components_raw.get("sprawl", 0.0)),
    )
    return BaselineEntry(
        id=FunctionId(path=path, qualname=qualname),
        score=float(score),
        components=components,
        fingerprint=fingerprint if isinstance(fingerprint, str) else None,
        signature=signature if isinstance(signature, str) else None,
        group=group if isinstance(group, str) else None,
    )


def _group_for_baseline_entry(
    entry: BaselineEntry,
    groups: Mapping[str, Sequence[str]] | None,
) -> str | None:
    if entry.group is not None:
        return entry.group
    if groups is None:
        return None
    return group_for_path(entry.id.path, groups)


def _unique_old_entries_by_fingerprint(old: Baseline) -> dict[str, BaselineEntry | None]:
    by_fingerprint: dict[str, BaselineEntry | None] = {}
    for entry in old.entries.values():
        if entry.fingerprint is None:
            continue
        if entry.fingerprint in by_fingerprint:
            by_fingerprint[entry.fingerprint] = None
        else:
            by_fingerprint[entry.fingerprint] = entry
    return by_fingerprint


def _current_fingerprint_counts(report: RiskReport) -> dict[str, int]:
    counts: dict[str, int] = {}
    for fn in report.functions:
        if fn.fingerprint is not None:
            counts[fn.fingerprint] = counts.get(fn.fingerprint, 0) + 1
    return counts


def _match_by_fingerprint(
    fn: FunctionRisk,
    old_by_fingerprint: dict[str, BaselineEntry | None],
    current_fingerprint_counts: dict[str, int],
    used_old_ids: set[FunctionId],
) -> BaselineEntry | None:
    if fn.fingerprint is None or current_fingerprint_counts.get(fn.fingerprint) != 1:
        return None
    entry = old_by_fingerprint.get(fn.fingerprint)
    if entry is None or entry.id in used_old_ids:
        return None
    return entry


def _unmatched_old_entries(
    old: Baseline,
    used_old_ids: set[FunctionId],
) -> list[BaselineEntry]:
    return [entry for fid, entry in old.entries.items() if fid not in used_old_ids]


@dataclass(frozen=True, slots=True)
class _Classification:
    """Result of looking up a current function against the baseline.

    `previous` is set when the function was matched (exact-id, unique
    body fingerprint, or weighted rename). `previous_id` is set only for
    rename / fingerprint matches — None for exact-id matches because no
    "move" happened. `ambiguous` is set only when the weighted matcher
    returned multiple plausible candidates.
    """

    previous: BaselineEntry | None
    previous_id: FunctionId | None
    match_confidence: float | None
    ambiguous: MatchResult | None


def _classify_against_baseline(
    fn: FunctionRisk,
    old: Baseline,
    old_by_fingerprint: dict[str, BaselineEntry | None],
    current_fingerprint_counts: dict[str, int],
    used_old_ids: set[FunctionId],
) -> _Classification:
    """Resolve the previous baseline entry, if any, for `fn`.

    Walks the matching ladder: exact id → unique body fingerprint →
    weighted rename. Returns either a matched `previous`, an ambiguous
    rename, or no match. The caller is responsible for mutating
    `used_old_ids` when consuming a match.
    """
    previous = old.entries.get(fn.id)
    if previous is not None:
        return _Classification(
            previous=previous,
            previous_id=None,
            match_confidence=None,
            ambiguous=None,
        )
    fingerprint_match = _match_by_fingerprint(
        fn, old_by_fingerprint, current_fingerprint_counts, used_old_ids
    )
    if fingerprint_match is not None:
        return _Classification(
            previous=fingerprint_match,
            previous_id=fingerprint_match.id,
            match_confidence=1.0,
            ambiguous=None,
        )
    result = match_rename(fn, _unmatched_old_entries(old, used_old_ids))
    if result.is_ambiguous:
        return _Classification(
            previous=None,
            previous_id=None,
            match_confidence=result.confidence,
            ambiguous=result,
        )
    if result.previous is not None:
        return _Classification(
            previous=result.previous,
            previous_id=result.previous.id,
            match_confidence=result.confidence,
            ambiguous=None,
        )
    return _Classification(
        previous=None,
        previous_id=None,
        match_confidence=None,
        ambiguous=None,
    )


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


def _component_regression(
    current: RiskComponents,
    previous: RiskComponents,
    *,
    tolerance: float,
) -> tuple[str, float, float, float] | None:
    regressions: list[tuple[str, float, float, float]] = []
    for name in (
        "coverage_gap",
        "structural_complexity",
        "branch_gap",
        "churn",
        "public_surface",
        "sprawl",
    ):
        previous_value = float(getattr(previous, name))
        current_value = float(getattr(current, name))
        delta = current_value - previous_value
        if delta > tolerance:
            regressions.append((name, previous_value, current_value, delta))
    if not regressions:
        return None
    return max(regressions, key=lambda item: item[3])
