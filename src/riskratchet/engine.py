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
        coverage_data = load_coverage_map(coverage_map)
        coverage_present = True
    elif coverage_path is not None:
        coverage_data = load_coverage(Path(coverage_path))
        coverage_present = True
    else:
        coverage_data = empty_coverage()
        coverage_present = False
    parsed_files: list[ParsedFile] = []
    function_risks: list[FunctionRisk] = []
    file_stats_list: list[FileStats] = []
    suppressed_functions = 0
    skipped_missing_coverage = 0

    for py_path in py_files:
        parsed = parse_file(py_path, root=root_path)
        if isinstance(parsed, ParseError):
            print(
                f"warning: skipping {parsed.path}: {parsed.message}",
                file=sys.stderr,
            )
            continue
        parsed_files.append(parsed)
        file_stats_list.append(parsed.file_stats)

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
    )


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
    for pattern in patterns:
        if "/" in pattern or "**" in pattern:
            if fnmatch(fn.id.path, pattern):
                return True
            continue
        if fnmatch(fn.id.qualname, pattern):
            return True
    return False
