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

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from riskratchet.matching import MatchResult, match_rename
from riskratchet.models import (
    Baseline,
    BaselineEntry,
    DiffReport,
    DiffStatus,
    FunctionId,
    FunctionRisk,
    RiskComponents,
    RiskReport,
)


def languages_not_scanned(old: Baseline, report: RiskReport) -> dict[str, int]:
    """Baseline languages this report holds no function for, with their entry counts.

    A `check` without the TypeScript backend over a mixed baseline silently gates only
    the Python half — those entries never reach the comparison, because `compare` has
    no "removed" concept. Pure and typer-free so the CLI and the pytest plugin render
    the same fact rather than each keeping its own copy of the rule. Empty when the
    report itself is empty: the empty-scan guards own that case with a better message.
    """
    if not report.functions:
        return {}
    scanned = {fn.language for fn in report.functions}
    counts: dict[str, int] = {}
    for entry in old.entries.values():
        if entry.language and entry.language not in scanned:
            counts[entry.language] = counts.get(entry.language, 0) + 1
    return dict(sorted(counts.items()))


def unscanned_baseline_files(
    diff_report: DiffReport,
    *,
    report: RiskReport,
    config_dir: Path,
    scan_roots: Sequence[Path],
) -> tuple[int, int]:
    """`(entries, files)` of baseline entries whose file the scan should have reached but did not.

    A REMOVED entry is a deletion, a deliberate subset, or a filter problem, and only the
    last one is worth a warning. The signal that isolates it: the entry's language was
    scanned, its file still exists, the file lies under a scanned root, and the file is
    absent from `report.files` — which since 0.3.6 lists every file the scan reached,
    including ones with zero functions, so a function deleted from a scanned file does
    not count. A file outside every scanned root is a subset by construction
    (`riskratchet check packages/api`); a file that is gone is a deletion. Containment
    is lexical, never resolved, so a symlinked scan root keeps the answer its keys give.

    Pure and typer-free so the CLI and the pytest plugin render the same fact.
    """
    scanned_languages = {fn.language for fn in report.functions}
    reached = {stats.path for stats in report.files}
    roots = [os.path.normpath(os.path.join(config_dir, root)) for root in scan_roots]
    entries = 0
    files: set[str] = set()
    for entry in diff_report.by_status(DiffStatus.REMOVED):
        previous = entry.previous
        language = previous.language if previous is not None else "python"
        if language not in scanned_languages or entry.id.path in reached:
            continue
        absolute = os.path.normpath(os.path.join(config_dir, entry.id.path))
        if not os.path.exists(absolute) or not any(_lexically_under(absolute, root) for root in roots):
            continue
        entries += 1
        files.add(entry.id.path)
    return entries, len(files)


def unscanned_files_message(entries: int, files: int) -> str:
    """The warning both doors print for `unscanned_baseline_files`; counts only, so it is
    safe under redaction."""
    entry_word = "entry lives" if entries == 1 else "entries live"
    file_word = "file that was" if files == 1 else "files that were"
    return (
        f"warning: {entries} baseline {entry_word} in {files} {file_word} under the scanned paths "
        "but not scanned this run (include / exclude?) — they are not being gated. "
        "Widen the filters, or run `riskratchet baseline` to drop them deliberately."
    )


def _lexically_under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


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
