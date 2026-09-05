"""Orchestration layer: walks files, gathers signals, builds a RiskReport.

The CLI and the future pytest plugin both call `analyze`; nothing here is
specific to argument parsing or output formatting. Parse errors are emitted
as warnings on stderr and the offending file is skipped.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from riskratchet._paths import relative_posix
from riskratchet.analysis import ParsedFile, ParseError, iter_python_files, parse_file
from riskratchet.complexity import complexity_for_file
from riskratchet.coverage import (
    CoverageData,
    MissingCoveragePolicy,
    MultiCoverageData,
    coverage_for_span,
    empty_coverage,
    load_coverage,
    load_coverage_map,
)
from riskratchet.git import DEFAULT_CHURN_WINDOW_DAYS, churn_for_function, collect_function_churn
from riskratchet.groups import group_for_path
from riskratchet.models import (
    ChurnStats,
    FileStats,
    FunctionId,
    FunctionRisk,
    RiskReport,
)
from riskratchet.scoring import compute_components, crap_score, resolve_weights, total_risk


def analyze(
    paths: Sequence[Path],
    *,
    root: Path | None = None,
    coverage_path: Path | None = None,
    coverage_map: Mapping[str, Path] | None = None,
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
    allow: Sequence[str] = (),
    use_git: bool = True,
    churn_days: int = DEFAULT_CHURN_WINDOW_DAYS,
    weights: Mapping[str, float] | None = None,
    missing_coverage_policy: MissingCoveragePolicy = MissingCoveragePolicy.PESSIMISTIC,
    groups: Mapping[str, Sequence[str]] | None = None,
    on_coverage_error: Any = None,
) -> RiskReport:
    """Analyze `paths` and return a full risk report.

    `paths` is interpreted relative to `root` (default: cwd) for both file
    discovery and coverage matching. Glob patterns in `include`/`exclude` are
    matched against root-relative POSIX paths.

    Pass either `coverage_path` (single coverage file) or `coverage_map` (one
    coverage file per repo-relative prefix). Passing both raises `ValueError`.
    """
    if coverage_path is not None and coverage_map:
        raise ValueError("coverage_path and coverage_map are mutually exclusive")

    root_path = (root or Path.cwd()).resolve()
    py_files = iter_python_files(
        [Path(p) for p in paths],
        root=root_path,
        include=list(include),
        exclude=list(exclude),
    )

    resolved_weights = resolve_weights(weights)
    coverage_data: CoverageData | MultiCoverageData
    if coverage_map:
        coverage_data = load_coverage_map(coverage_map, on_error=on_coverage_error)
        coverage_present = True
    elif coverage_path is not None:
        coverage_data = load_coverage(Path(coverage_path))
        coverage_present = True
    else:
        coverage_data = empty_coverage()
        coverage_present = False
    function_risks: list[FunctionRisk] = []
    suppressed_functions = 0
    skipped_missing_coverage = 0
    parsed_files, file_stats_list, skipped_generated_files = _parse_sources(py_files, root_path)

    churn_by_function = collect_function_churn(
        root_path,
        [(fn.id, fn.span) for parsed in parsed_files for fn in parsed.functions],
        days=churn_days,
        enabled=use_git,
    )

    for parsed in parsed_files:
        file_coverage = coverage_data.lookup(parsed.relative_path)
        if (
            coverage_present
            and file_coverage is None
            and missing_coverage_policy is MissingCoveragePolicy.SKIP
        ):
            function_risks_skipped = len(parsed.functions)
            function_risks.extend([])
            skipped_missing_coverage += function_risks_skipped
            continue
        if coverage_present and file_coverage is None:
            print(
                f"warning: {parsed.relative_path} has no matching entry in coverage data",
                file=sys.stderr,
            )
        risks = _risks_for_file(
            parsed,
            coverage_data,
            churn_by_function,
            resolved_weights,
            missing_coverage_policy=missing_coverage_policy,
            groups=groups or {},
        )
        for risk in risks:
            if _is_allowed(risk, allow):
                suppressed_functions += 1
            else:
                function_risks.append(risk)

    return RiskReport(
        functions=tuple(function_risks),
        files=tuple(file_stats_list),
        coverage_status="present" if coverage_present else "missing",
        suppressed_functions=suppressed_functions,
        skipped_missing_coverage=skipped_missing_coverage,
        analyzed_functions=len(function_risks) + suppressed_functions,
        skipped_generated_files=skipped_generated_files,
    )


def _parse_sources(py_files: list[Path], root_path: Path) -> tuple[list[ParsedFile], list[FileStats], int]:
    """Parse every discovered file into the ones to score, the stats of every file reached,
    and the count of generated files.

    `files` means every file the scan reached: a syntax-error file is listed with zero
    functions (as the TypeScript backend already did) and a `@generated` file is listed,
    counted, and not scored — so a skipped population shows up in `total_files` rather
    than vanishing.
    """
    parsed_files: list[ParsedFile] = []
    file_stats_list: list[FileStats] = []
    skipped_generated_files = 0
    for py_path in py_files:
        parsed = parse_file(py_path, root=root_path)
        if isinstance(parsed, ParseError):
            print(f"warning: skipping {parsed.path}: {parsed.message}", file=sys.stderr)
            file_stats_list.append(
                FileStats(
                    path=relative_posix(parsed.path, root_path),
                    total_lines=parsed.total_lines,
                    function_count=0,
                )
            )
            continue
        file_stats_list.append(parsed.file_stats)
        if parsed.generated:
            skipped_generated_files += 1
            continue
        parsed_files.append(parsed)
    return parsed_files, file_stats_list, skipped_generated_files


def _risks_for_file(
    parsed: ParsedFile,
    coverage_data: CoverageData | MultiCoverageData,
    churn_by_function: dict[FunctionId, ChurnStats],
    weights: Mapping[str, float],
    *,
    missing_coverage_policy: MissingCoveragePolicy,
    groups: Mapping[str, Sequence[str]],
) -> list[FunctionRisk]:
    complexity_by_line = complexity_for_file(parsed)
    file_coverage = coverage_data.lookup(parsed.relative_path)

    risks: list[FunctionRisk] = []
    for fn in parsed.functions:
        complexity = complexity_by_line[fn.span.start_line]
        coverage = coverage_for_span(file_coverage, fn.span, missing_policy=missing_coverage_policy)
        function_churn = churn_for_function(churn_by_function, fn.id)
        components = compute_components(
            is_public=fn.is_public,
            span=fn.span,
            complexity=complexity,
            coverage=coverage,
            churn=function_churn,
            file_stats=parsed.file_stats,
        )
        risks.append(
            FunctionRisk(
                id=fn.id,
                span=fn.span,
                is_public=fn.is_public,
                complexity=complexity,
                coverage=coverage,
                churn=function_churn,
                file_stats=parsed.file_stats,
                components=components,
                score=total_risk(components, weights=weights),
                crap=crap_score(complexity, coverage),
                fingerprint=fn.fingerprint,
                signature=fn.signature,
                group=group_for_path(fn.id.path, groups),
            )
        )
    return risks


def _is_allowed(fn: FunctionRisk, patterns: Sequence[str]) -> bool:
    """True when any `allow` pattern suppresses this function."""
    return any(pattern_matches(p, fn.id.as_target(), fn.id.path, fn.id.qualname) for p in patterns)


def pattern_matches(pattern: str, target: str, path: str, qualname: str) -> bool:
    """Pick which of a function's three names an `allow` pattern is matched against.

    A pattern containing `::` matches the full `path::qualname` target — the canonical
    form `explain` requires and `check`/`diff` print. Without that case, copying a
    target out of a report and pasting it into `allow` suppressed nothing, silently:
    patterns were matched against the path *or* the qualname, never the target, so the
    one spelling riskratchet itself emits was the one that could not work. `allow` also
    removes entries from the baseline, so a no-op suppression means debt the user
    believes is parked is still being ratcheted.

    Shared shape with `typescript_engine.pattern_matches`; kept in step by
    `test_both_backends_suppress_the_same_patterns`.
    """
    if "::" in pattern:
        return fnmatch(target, pattern)
    if "/" in pattern or "**" in pattern:
        return fnmatch(path, pattern)
    return fnmatch(qualname, pattern)
