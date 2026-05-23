"""Orchestration layer: walks files, gathers signals, builds a RiskReport.

The CLI and the future pytest plugin both call `analyze`; nothing here is
specific to argument parsing or output formatting. Parse errors are emitted
as warnings on stderr and the offending file is skipped.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from riskratchet.analysis import ParsedFile, ParseError, iter_python_files, parse_file
from riskratchet.complexity import complexity_for_file
from riskratchet.coverage import (
    CoverageData,
    coverage_for_span,
    empty_coverage,
    load_coverage,
)
from riskratchet.git import churn_for_function, collect_function_churn
from riskratchet.models import (
    ChurnStats,
    FileStats,
    FunctionId,
    FunctionRisk,
    RiskReport,
)
from riskratchet.scoring import compute_components, crap_score, total_risk


def analyze(
    paths: Sequence[Path],
    *,
    root: Path | None = None,
    coverage_path: Path | None = None,
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
    use_git: bool = True,
) -> RiskReport:
    """Analyze `paths` and return a full risk report.

    `paths` is interpreted relative to `root` (default: cwd) for both file
    discovery and coverage matching. Glob patterns in `include`/`exclude` are
    matched against root-relative POSIX paths.
    """
    root_path = (root or Path.cwd()).resolve()
    py_files = iter_python_files(
        [Path(p) for p in paths],
        root=root_path,
        include=list(include),
        exclude=list(exclude),
    )

    coverage_data = load_coverage(Path(coverage_path)) if coverage_path is not None else empty_coverage()
    parsed_files: list[ParsedFile] = []
    function_risks: list[FunctionRisk] = []
    file_stats_list: list[FileStats] = []

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
        enabled=use_git,
    )

    for parsed in parsed_files:
        function_risks.extend(_risks_for_file(parsed, coverage_data, churn_by_function))

    return RiskReport(
        functions=tuple(function_risks),
        files=tuple(file_stats_list),
        coverage_status="present" if coverage_path is not None else "missing",
    )


def _risks_for_file(
    parsed: ParsedFile,
    coverage_data: CoverageData,
    churn_by_function: dict[FunctionId, ChurnStats],
) -> list[FunctionRisk]:
    complexity_by_line = complexity_for_file(parsed)
    file_coverage = coverage_data.lookup(parsed.relative_path)

    risks: list[FunctionRisk] = []
    for fn in parsed.functions:
        complexity = complexity_by_line[fn.span.start_line]
        coverage = coverage_for_span(file_coverage, fn.span)
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
                score=total_risk(components),
                crap=crap_score(complexity, coverage),
                fingerprint=fn.fingerprint,
            )
        )
    return risks
