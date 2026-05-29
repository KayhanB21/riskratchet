"""Shared baseline-matching ladder and component-regression policy.

This leaf holds the logic consumed by *both* `compare` and `diff`:

- the exact-id -> unique-fingerprint -> weighted-rename matching ladder
  (`_classify_against_baseline` and its fingerprint helpers), which
  resolves the previous baseline entry for a current function;
- `_component_regression`, the per-component tolerance check.

Keeping it here (rather than in `compare` or `diff`) is what lets those
two family modules stay independent of each other. The rename matcher
itself lives in the top-level `riskratchet.matching` module because
`analysis` also depends on its `signature_fingerprint`.
"""

from __future__ import annotations

from dataclasses import dataclass

from riskratchet.matching import MatchResult, match_rename
from riskratchet.models import (
    Baseline,
    BaselineEntry,
    FunctionId,
    FunctionRisk,
    RiskComponents,
    RiskReport,
)


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
