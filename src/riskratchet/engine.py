"""Orchestration layer: walks files, gathers signals, builds a RiskReport.

The CLI and the pytest plugin both call `analyze`; nothing here is specific
to argument parsing or output formatting. A file that fails to parse is
skipped, and a file with no coverage entry is reported.

Both of those are disclosures *about the code under analysis*, so the caller
formats them: pass `on_warning` / `on_error` and the engine hands you the
`Path` and the reason, leaving you to relativize and redact. Without a
callback the engine still writes to stderr, so a direct library call is never
silent -- the same rule `git.py` states for churn errors. Before 0.3.8 there
was no callback at all: these two lines were `print()` calls, which is how
they went on naming real modules under `redact_paths`.
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
from riskratchet.git import (
    DEFAULT_CHURN_WINDOW_DAYS,
    churn_for_function,
    churn_is_available,
    collect_function_churn,
)
from riskratchet.groups import group_for_path
from riskratchet.models import (
    ChurnStats,
    FileStats,
    FunctionId,
    FunctionRisk,
    RiskReport,
    ScoringInputs,
    UnscoredCause,
)
from riskratchet.scoring import (
    SCORING_MODEL_VERSION,
    compute_components,
    crap_score,
    resolve_weights,
    total_risk,
)


def _say_warning(on_warning: Any, path: Path, root: Path, message: str) -> None:
    """Report a file the scan reached but could not match to coverage.

    Hands the caller the absolute `Path` so it can pick the spelling: a redacted warning
    only correlates with its report row if the caller hashes `relative_posix(path,
    config_dir)`, because that is the spelling `FunctionId.path` carries and
    `redact_function_id` hashes. Hashing anything else produces a digest that matches
    nothing, which is a warning no one can act on.
    """
    if on_warning is not None:
        on_warning(path, message)
        return
    print(f"warning: {relative_posix(path, root)} {message}", file=sys.stderr)


def _say_error(on_error: Any, path: Path, root: Path, message: str) -> None:
    """Report a file that failed to parse. Same contract as `_say_warning`."""
    if on_error is not None:
        on_error(path, message)
        return
    print(f"warning: skipping {relative_posix(path, root)}: {message}", file=sys.stderr)


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
    on_churn_error: Any = None,
    on_warning: Any = None,
    on_error: Any = None,
) -> RiskReport:
    """Analyze `paths` and return a full risk report.

    `paths` is interpreted relative to `root` (default: cwd) for both file
    discovery and coverage matching. Glob patterns in `include`/`exclude` are
    matched against root-relative POSIX paths.

    Pass either `coverage_path` (single coverage file) or `coverage_map` (one
    coverage file per repo-relative prefix). Passing both raises `ValueError`.

    `on_warning(path, message)` reports a file the scan reached but could not
    match to coverage; `on_error(path, message)` reports one that failed to
    parse. Both receive the absolute `Path`, matching `analyze_typescript`'s
    `on_error`, because the caller -- not the engine -- knows which root the
    path must be relative to and whether redaction is active. Omit them and
    the engine writes the same two lines to stderr it always has.
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
    unscored_functions: list[tuple[FunctionId, UnscoredCause]] = []
    parsed_files, file_stats_list, unscored_files = _parse_sources(py_files, root_path, on_error)

    churn_by_function = collect_function_churn(
        root_path,
        [(fn.id, fn.span) for parsed in parsed_files for fn in parsed.functions],
        days=churn_days,
        enabled=use_git,
        on_error=on_churn_error,
    )

    for parsed in parsed_files:
        file_coverage = coverage_data.lookup(parsed.relative_path)
        if (
            coverage_present
            and file_coverage is None
            and missing_coverage_policy is MissingCoveragePolicy.SKIP
        ):
            skipped_missing_coverage += len(parsed.functions)
            unscored_functions.extend((fn.id, UnscoredCause.MISSING_COVERAGE) for fn in parsed.functions)
            continue
        if coverage_present and file_coverage is None:
            _say_warning(on_warning, parsed.path, root_path, "has no matching entry in coverage data")
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
                unscored_functions.append((risk.id, UnscoredCause.SUPPRESSED))
            else:
                function_risks.append(risk)

    return RiskReport(
        functions=tuple(function_risks),
        files=tuple(file_stats_list),
        coverage_status="present" if coverage_present else "missing",
        suppressed_functions=suppressed_functions,
        skipped_missing_coverage=skipped_missing_coverage,
        analyzed_functions=len(function_risks) + suppressed_functions,
        skipped_generated_files=_count_generated(unscored_files),
        unscored_functions=tuple(unscored_functions),
        unscored_files=tuple(unscored_files),
        scoring=ScoringInputs(
            model=SCORING_MODEL_VERSION,
            weights=resolved_weights,
            churn_window_days=churn_days,
            churn_available=churn_is_available(root_path, enabled=use_git),
        ),
    )


def _count_generated(unscored_files: list[tuple[str, UnscoredCause]]) -> int:
    return sum(cause is UnscoredCause.GENERATED for _, cause in unscored_files)


def _parse_sources(
    py_files: list[Path], root_path: Path, on_error: Any = None
) -> tuple[list[ParsedFile], list[FileStats], list[tuple[str, UnscoredCause]]]:
    """Parse every discovered file into the ones to score, the stats of every file reached,
    and the files reached but not scored, with the reason.

    `files` means every file the scan reached: a syntax-error file is listed with zero
    functions (as the TypeScript backend already did) and a `@generated` file is listed,
    counted, and not scored — so a skipped population shows up in `total_files` rather
    than vanishing.
    """
    parsed_files: list[ParsedFile] = []
    file_stats_list: list[FileStats] = []
    unscored_files: list[tuple[str, UnscoredCause]] = []
    for py_path in py_files:
        parsed = parse_file(py_path, root=root_path)
        if isinstance(parsed, ParseError):
            _say_error(on_error, parsed.path, root_path, parsed.message)
            rel = relative_posix(parsed.path, root_path)
            file_stats_list.append(FileStats(path=rel, total_lines=parsed.total_lines, function_count=0))
            unscored_files.append((rel, UnscoredCause.PARSE_ERROR))
            continue
        file_stats_list.append(parsed.file_stats)
        if parsed.generated:
            unscored_files.append((parsed.relative_path, UnscoredCause.GENERATED))
            continue
        parsed_files.append(parsed)
    return parsed_files, file_stats_list, unscored_files


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
