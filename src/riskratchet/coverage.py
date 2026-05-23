"""Parse `coverage json` output and map it to function spans.

`load_coverage` reads the JSON file once. `coverage_for_span` intersects the
per-file line/branch data with a function's line range. Files absent from the
coverage payload are treated as fully uncovered (0%) so they ratchet risk up
rather than passing silently.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from riskratchet.models import CoverageStats, FunctionSpan


@dataclass(frozen=True)
class CoverageData:
    """Indexed view of `coverage json` output.

    `_files` is keyed by the original path strings from the JSON; `_by_suffix`
    accelerates lookups by relative path or basename when the coverage file
    uses absolute paths from a different working directory.
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
        for candidate in candidates:
            if candidate.endswith(relative_posix_path):
                return self._files[candidate]
        if len(candidates) == 1:
            return self._files[candidates[0]]
        return None


def load_coverage(path: Path) -> CoverageData:
    """Load coverage.json from disk. Raises FileNotFoundError if missing."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read coverage file {path}: {exc}") from exc

    files_section = raw.get("files")
    if not isinstance(files_section, dict):
        raise ValueError(f"coverage file {path} has no `files` section")

    files: dict[str, dict[str, Any]] = {}
    by_suffix: dict[str, list[str]] = {}
    for original_path, payload in files_section.items():
        if not isinstance(payload, dict):
            continue
        normalized = original_path.replace("\\", "/")
        files[normalized] = payload
        by_suffix.setdefault(_basename(normalized), []).append(normalized)
    return CoverageData(_files=files, _by_suffix=by_suffix)


def empty_coverage() -> CoverageData:
    return CoverageData(_files={}, _by_suffix={})


def coverage_for_span(
    file_coverage: dict[str, Any] | None,
    span: FunctionSpan,
) -> CoverageStats:
    """Compute line/branch coverage for the lines inside `span`.

    When the file is missing from coverage data entirely the function is
    treated as uncovered (0%). When the file is present but no executable
    lines fall inside the span, the function is treated as fully covered:
    there is nothing for tests to exercise.
    """
    if file_coverage is None:
        return CoverageStats.uncovered()

    span_lines = set(range(span.start_line, span.end_line + 1))
    executed = {int(x) for x in file_coverage.get("executed_lines", [])}
    missing = {int(x) for x in file_coverage.get("missing_lines", [])}
    executed_in_span = executed & span_lines
    missing_in_span = missing & span_lines
    measured = executed_in_span | missing_in_span

    if not measured:
        return CoverageStats(line_coverage=1.0, branch_coverage=None)

    line_coverage = len(executed_in_span) / len(measured)

    branch_coverage, missing_branches = _branch_stats(file_coverage, span_lines)

    return CoverageStats(
        line_coverage=line_coverage,
        branch_coverage=branch_coverage,
        missing_lines=tuple(sorted(missing_in_span)),
        missing_branches=missing_branches,
    )


def _branch_stats(
    file_coverage: dict[str, Any],
    span_lines: set[int],
) -> tuple[float | None, tuple[tuple[int, int], ...]]:
    executed_branches = file_coverage.get("executed_branches")
    missing_branches = file_coverage.get("missing_branches")
    if executed_branches is None and missing_branches is None:
        return None, ()

    executed_pairs = _branch_pairs_in_span(executed_branches or [], span_lines)
    missing_pairs = _branch_pairs_in_span(missing_branches or [], span_lines)
    total = len(executed_pairs) + len(missing_pairs)
    if total == 0:
        return None, ()
    coverage = len(executed_pairs) / total
    return coverage, tuple(missing_pairs)


def _branch_pairs_in_span(
    pairs: list[Any],
    span_lines: set[int],
) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for pair in pairs:
        if not isinstance(pair, list) or len(pair) != 2:
            continue
        src, dst = int(pair[0]), int(pair[1])
        if src in span_lines:
            out.append((src, dst))
    return out


def _basename(posix_path: str) -> str:
    return posix_path.rsplit("/", 1)[-1]
