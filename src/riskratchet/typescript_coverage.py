"""EXPERIMENTAL: map Istanbul/nyc coverage onto discovered TypeScript function spans
(P20, slice 3, since 0.2.13).

Discovery (`typescript.py`) tells us *where* each TypeScript function is; this module
answers *how well tested* it is, by reading an Istanbul `coverage-final.json` (the dominant
TS/JS coverage artifact, produced by `nyc`/`c8`/Jest's `--coverage`) and intersecting its
per-statement / per-branch data with a function's line span. The result is the same
language-neutral `CoverageStats(line_coverage, branch_coverage, missing_lines,
missing_branches)` the Python backend produces in `coverage.py`, so a future TS scoring
pipeline can consume it unchanged.

This is informational only — like discovery, it never feeds scoring, baseline, or gating in
0.2.x. It is reached solely through the `scan --experimental-typescript --ts-coverage` path
and imports no tree-sitter (it is pure JSON), so a Python-only install is unaffected.

Istanbul format (mirrors what `nyc` writes): a top-level object keyed by absolute file path,
each value carrying `statementMap`/`s` (per-statement ranges + hit counts), `branchMap`/`b`
(per-branch arm ranges + per-arm hit counts), and `fnMap`/`f`. All map keys and `s`/`b`/`f`
keys are JSON strings; lines are 1-based, columns 0-based. Unknown keys (`_coverageSchema`,
`hash`, `inputSourceMap`) are tolerated.

LCOV is intentionally out of scope for this slice (Istanbul JSON only); it is closer to
`coverage.py`'s shape and folds in later if demand appears.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from riskratchet.coverage import MissingCoveragePolicy
from riskratchet.models import CoverageStats, FunctionSpan

# Branch `type`s Istanbul reports. All are counted toward the branch denominator (faithful to
# raw nyc totals) — including `default-arg`, which the Python backend has no analog for.
# Documented in docs/language-backend-contract.md §2.


@dataclass(frozen=True)
class IstanbulCoverageData:
    """Indexed view of an Istanbul `coverage-final.json`.

    `_files` is keyed by the original (usually absolute) path strings; `_by_suffix`
    accelerates lookups by relative path or basename, since the report's paths are absolute
    and machine-specific while discovery works in repo-relative posix paths. Mirrors
    `coverage.CoverageData`.
    """

    _files: dict[str, dict[str, Any]]
    _by_suffix: dict[str, list[str]]

    @property
    def file_paths(self) -> tuple[str, ...]:
        return tuple(self._files.keys())

    def lookup(self, relative_posix_path: str) -> dict[str, Any] | None:
        if relative_posix_path in self._files:
            return self._files[relative_posix_path]
        candidates = self._by_suffix.get(_basename(relative_posix_path))
        if not candidates:
            return None
        # Longest-suffix wins: prefer the candidate that matches the most trailing path.
        best: str | None = None
        for candidate in candidates:
            matches = candidate == relative_posix_path or candidate.endswith("/" + relative_posix_path)
            if matches and (best is None or len(candidate) > len(best)):
                best = candidate
        return self._files[best] if best is not None else None


def load_istanbul_coverage(path: Any) -> IstanbulCoverageData:
    """Load an Istanbul `coverage-final.json` from disk. Raises FileNotFoundError if missing,
    ValueError on unreadable/non-object content (mirrors `coverage.load_coverage`)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read Istanbul coverage file {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"Istanbul coverage file {path} is not a JSON object of file entries")

    files: dict[str, dict[str, Any]] = {}
    by_suffix: dict[str, list[str]] = {}
    for original_path, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        normalized = original_path.replace("\\", "/")
        files[normalized] = payload
        by_suffix.setdefault(_basename(normalized), []).append(normalized)
    return IstanbulCoverageData(_files=files, _by_suffix=by_suffix)


def empty_istanbul_coverage() -> IstanbulCoverageData:
    return IstanbulCoverageData(_files={}, _by_suffix={})


def coverage_for_ts_span(
    file_coverage: dict[str, Any] | None,
    span: FunctionSpan,
    *,
    missing_policy: MissingCoveragePolicy = MissingCoveragePolicy.PESSIMISTIC,
) -> CoverageStats:
    """Compute line/branch coverage for the lines inside `span` from one file's Istanbul data.

    Line coverage keys on each statement's `start.line` only (not its end line), collapsing
    statements that share a line with `max` hit count — exactly what
    `istanbul-lib-coverage.getLineCoverage` does. A file missing from the report follows
    `missing_policy` (PESSIMISTIC → 0%, OPTIMISTIC → 100%, SKIP → fully covered/no-data). A
    file present but with no measurable statements in the span is treated as fully covered:
    there is nothing for tests to exercise.
    """
    if file_coverage is None:
        if missing_policy is MissingCoveragePolicy.PESSIMISTIC:
            return CoverageStats.uncovered()
        # OPTIMISTIC and SKIP both decline to penalize an unmeasured file in this
        # informational path; the CLI listing renders SKIP as "no coverage data".
        return CoverageStats(line_coverage=1.0, branch_coverage=None)

    line_coverage, missing_lines = _line_stats(file_coverage, span)
    if line_coverage is None:
        return CoverageStats(line_coverage=1.0, branch_coverage=None)

    branch_coverage, missing_branches = _branch_stats(file_coverage, span)
    return CoverageStats(
        line_coverage=line_coverage,
        branch_coverage=branch_coverage,
        missing_lines=missing_lines,
        missing_branches=missing_branches,
    )


def _line_stats(
    file_coverage: dict[str, Any],
    span: FunctionSpan,
) -> tuple[float | None, tuple[int, ...]]:
    """Per-line executed/missing from `statementMap` + `s`. Returns (None, ()) when no
    statement starts inside the span (nothing measurable)."""
    statement_map = file_coverage.get("statementMap")
    hits = file_coverage.get("s")
    if not isinstance(statement_map, dict) or not isinstance(hits, dict):
        return None, ()

    line_hits: dict[int, int] = {}
    for sid, rng in statement_map.items():
        line = _start_line(rng)
        if line is None or not (span.start_line <= line <= span.end_line):
            continue
        count = _as_int(hits.get(sid))
        line_hits[line] = max(line_hits.get(line, 0), count)

    if not line_hits:
        return None, ()

    executed = {line for line, count in line_hits.items() if count > 0}
    missing = tuple(sorted(line for line in line_hits if line not in executed))
    return len(executed) / len(line_hits), missing


def _branch_stats(
    file_coverage: dict[str, Any],
    span: FunctionSpan,
) -> tuple[float | None, tuple[tuple[int, int], ...]]:
    """Branch coverage for branches whose `loc.start.line` falls in the span. `b[id]` is an
    array of arm hit counts positionally aligned to `branchMap[id].locations`; an arm with
    count > 0 is executed. Returns (None, ()) when no branch is measured in the span."""
    branch_map = file_coverage.get("branchMap")
    hits = file_coverage.get("b")
    if not isinstance(branch_map, dict) or not isinstance(hits, dict):
        return None, ()

    total = 0
    covered = 0
    missing: list[tuple[int, int]] = []
    for bid, branch in branch_map.items():
        if not isinstance(branch, dict):
            continue
        line = _start_line(branch.get("loc"))
        if line is None or not (span.start_line <= line <= span.end_line):
            continue
        arm_counts = hits.get(bid)
        if not isinstance(arm_counts, list):
            continue
        for index, raw in enumerate(arm_counts):
            total += 1
            if _as_int(raw) > 0:
                covered += 1
            else:
                missing.append((line, index))

    if total == 0:
        return None, ()
    return covered / total, tuple(missing)


def _start_line(rng: Any) -> int | None:
    if not isinstance(rng, dict):
        return None
    start = rng.get("start")
    if not isinstance(start, dict):
        return None
    line = start.get("line")
    return int(line) if isinstance(line, int) else None


def _as_int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) else 0


def _basename(posix_path: str) -> str:
    return posix_path.rsplit("/", 1)[-1]
