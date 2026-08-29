"""Scored TypeScript backend + report merge (P20 capstone, 0.3.0).

The routing seam that promotes TypeScript from informational discovery to a *scored* backend. It
mirrors `engine.analyze` for Python, but over the TypeScript discovery/coverage/identity modules and
with the TS complexity calibration:

- `analyze_typescript()` discovers functions (`typescript.analyze_ts_file`), attaches optional
  Istanbul/LCOV coverage (`typescript_coverage`), narrows the public surface to the entry barrel
  (`typescript_exports`), reuses the language-neutral churn (`git`) and groups (`groups`), and scores
  each function into a `FunctionRisk(language="typescript")` using
  `TYPESCRIPT_COMPLEXITY_CALIBRATION` (B2) — so the normalized complexity component is language-fair.
- `merge_reports()` concatenates a Python report and a TypeScript report into one. Their function ids
  can never collide (`.py` vs `.ts`/`.tsx` paths), so the merge is a pure append; Python first, then
  TypeScript, for a deterministic order.

**Import-isolation invariant.** `engine.py` imports zero TypeScript modules, so a Python-only install
never reaches tree-sitter. This module *does* import the TypeScript backend, but tree-sitter itself
is still imported lazily (inside `typescript._require_tree_sitter`, only when discovery runs). The
orchestration entry `pipeline.build_report` imports this module lazily (only when `typescript=True`),
so a default Python-only scan never imports it at all.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from riskratchet import typescript as ts
from riskratchet import typescript_coverage as tscov
from riskratchet import typescript_exports as tsx
from riskratchet._paths import relative_posix
from riskratchet.coverage import MissingCoveragePolicy
from riskratchet.engine import pattern_matches
from riskratchet.git import DEFAULT_CHURN_WINDOW_DAYS, churn_for_function, collect_function_churn
from riskratchet.groups import group_for_path
from riskratchet.models import (
    ComplexityStats,
    CoverageStats,
    FileStats,
    FunctionRisk,
    RiskReport,
)
from riskratchet.scoring import (
    TYPESCRIPT_COMPLEXITY_CALIBRATION,
    compute_components,
    crap_score,
    resolve_weights,
    total_risk,
)

WarnFn = Any  # Callable[[str], None] | None — kept loose so callers can pass a bound typer helper.


def analyze_typescript(
    paths: Sequence[Path],
    *,
    root: Path | None = None,
    ts_coverage_paths: Sequence[Path] | None = None,
    ts_entries: Sequence[Path] | None = None,
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
    allow: Sequence[str] = (),
    use_git: bool = True,
    churn_days: int = DEFAULT_CHURN_WINDOW_DAYS,
    weights: Mapping[str, float] | None = None,
    missing_coverage_policy: MissingCoveragePolicy = MissingCoveragePolicy.PESSIMISTIC,
    groups: Mapping[str, Sequence[str]] | None = None,
    on_warning: WarnFn = None,
    on_error: Any = None,
) -> RiskReport:
    """Discover, enrich, and **score** the TypeScript functions under `paths`.

    Mirrors `engine.analyze` for the TS backend. `ts_coverage_paths` are Istanbul/LCOV reports
    (auto-detected/merged); `ts_entries` narrow the public surface to a package entry barrel. Coverage
    absent for a file follows `missing_coverage_policy` exactly as the Python path does
    (asserted by the cross-backend policy-parity test).
    """
    root_path = (root or Path.cwd()).resolve()
    resolved_weights = resolve_weights(weights)
    files = ts.iter_typescript_files(
        [Path(p) for p in paths], root=root_path, include=list(include), exclude=list(exclude)
    )

    coverage_paths = list(ts_coverage_paths or [])
    coverage = tscov.empty_istanbul_coverage()
    if coverage_paths:
        # strict: a report named for a *scoring* run must be readable. `_build_report_or_exit`
        # turns the ValueError into an exit-2 setup error, as it does for `--coverage`.
        coverage = tscov.load_ts_coverage_files(coverage_paths, on_error=on_error, strict=True)
    # Derive this from the data actually loaded, not from the paths we were handed.
    # `load_ts_coverage_files` reports an unreadable report through `on_error` and
    # returns an empty view, so keying off the request made "the report could not be
    # read" indistinguishable from "the report measured no files" — and under
    # `missing_coverage = skip` that dropped every TypeScript function and exited 0.
    has_coverage = bool(coverage.file_paths)

    discovered: list[Any] = []
    modules: dict[str, Any] = {}
    file_line_counts: dict[str, int] = {}
    file_fn_counts: dict[str, int] = {}
    unmeasured_files = 0
    for path in files:
        found, exports = ts.analyze_ts_file(path, root=root_path, on_error=on_error)
        rel = relative_posix(path, root_path)
        modules[rel] = exports
        file_line_counts[rel] = _count_lines(path)
        file_fn_counts[rel] = len(found)
        if has_coverage:
            file_cov = coverage.lookup(rel)
            if file_cov is None and found:
                unmeasured_files += 1
            found = _enrich_coverage(found, file_cov, rel, on_warning)
        discovered.extend(found)
    if unmeasured_files:
        _warn(on_warning, f"{unmeasured_files} file(s) had no coverage entry (scored without coverage)")

    discovered = _narrow_public(discovered, files, root_path, ts_entries, modules, on_warning)

    churn_by_function = collect_function_churn(
        root_path,
        [(fn.id, fn.span) for fn in discovered],
        days=churn_days,
        enabled=use_git,
    )

    risks: list[FunctionRisk] = []
    suppressed = 0
    skipped_missing_coverage = 0
    for fn in discovered:
        if _is_allowed(fn.id.path, fn.id.qualname, allow):
            suppressed += 1
            continue
        coverage_stats = _resolve_coverage(fn.coverage, has_coverage, missing_coverage_policy)
        if coverage_stats is None:  # SKIP policy, file absent from coverage
            skipped_missing_coverage += 1
            continue
        rel = fn.id.path
        file_stats = FileStats(
            path=rel, total_lines=file_line_counts.get(rel, 0), function_count=file_fn_counts.get(rel, 0)
        )
        complexity = fn.complexity if fn.complexity is not None else ComplexityStats(cyclomatic=1)
        function_churn = churn_for_function(churn_by_function, fn.id)
        components = compute_components(
            is_public=fn.is_public,
            span=fn.span,
            complexity=complexity,
            coverage=coverage_stats,
            churn=function_churn,
            file_stats=file_stats,
            complexity_calibration=TYPESCRIPT_COMPLEXITY_CALIBRATION,
        )
        risks.append(
            FunctionRisk(
                id=fn.id,
                span=fn.span,
                is_public=fn.is_public,
                complexity=complexity,
                coverage=coverage_stats,
                churn=function_churn,
                file_stats=file_stats,
                components=components,
                score=total_risk(components, weights=resolved_weights),
                crap=crap_score(complexity, coverage_stats),
                fingerprint=fn.fingerprint,
                signature=fn.signature,
                group=group_for_path(rel, groups or {}),
                language="typescript",
            )
        )

    file_stats_list = tuple(
        FileStats(path=rel, total_lines=file_line_counts[rel], function_count=file_fn_counts.get(rel, 0))
        for rel in sorted(file_line_counts)
    )
    return RiskReport(
        functions=tuple(risks),
        files=file_stats_list,
        coverage_status="present" if has_coverage else "missing",
        suppressed_functions=suppressed,
        skipped_missing_coverage=skipped_missing_coverage,
        analyzed_functions=len(risks) + suppressed,
    )


def merge_reports(python: RiskReport, typescript: RiskReport) -> RiskReport:
    """Concatenate a Python and a TypeScript report into one. Ids never collide (`.py` vs `.ts`
    paths), so this is a pure append — Python functions first, then TypeScript, for determinism.
    Scalar metadata is summed; `coverage_status` is "present" if either backend measured coverage."""
    both_status = (python.coverage_status, typescript.coverage_status)
    return RiskReport(
        functions=python.functions + typescript.functions,
        files=python.files + typescript.files,
        coverage_status="present" if "present" in both_status else python.coverage_status,
        suppressed_functions=python.suppressed_functions + typescript.suppressed_functions,
        skipped_missing_coverage=python.skipped_missing_coverage + typescript.skipped_missing_coverage,
        analyzed_functions=(python.analyzed_functions or 0) + (typescript.analyzed_functions or 0),
    )


def _resolve_coverage(
    coverage: CoverageStats | None,
    has_coverage: bool,
    policy: MissingCoveragePolicy,
) -> CoverageStats | None:
    """Resolve a discovered function's coverage into the stats the scorer consumes.

    `coverage is None` means unmeasured (no report, file absent, or misaligned). Mirrors the Python
    `coverage.coverage_for_span` missing-file policy: OPTIMISTIC → not penalized, anything else → 0%,
    except that SKIP drops the function (returns None) when a report exists but the file is absent
    from it.

    Only OPTIMISTIC may score an unmeasured function as covered. SKIP used to fall through to the
    same branch, so a `--typescript` run with no TS report scored every function 100% covered while
    the Python backend scored the same situation 0% — zeroing `coverage_gap` and `public_surface`,
    40% of the weight, for TypeScript only.
    """
    if coverage is not None:
        return coverage
    if has_coverage and policy is MissingCoveragePolicy.SKIP:
        return None
    if policy is MissingCoveragePolicy.OPTIMISTIC:
        return CoverageStats(line_coverage=1.0, branch_coverage=None)
    return CoverageStats.uncovered()


def _enrich_coverage(found: list[Any], file_cov: Any, rel: str, on_warning: WarnFn) -> list[Any]:
    """Attach coverage to one file's functions, or leave `coverage=None` when the file is absent or
    its line numbers intersect no discovered span (compiled-JS misalignment; warned)."""
    if file_cov is None:
        return [replace(fn, coverage=None) for fn in found]
    if found and not tscov.spans_cover_any_statement(file_cov, [fn.span for fn in found]):
        _warn(
            on_warning,
            f"{rel}: coverage line numbers don't intersect any discovered function "
            "— likely measured on compiled JS, not source (source maps?); coverage omitted",
        )
        return [replace(fn, coverage=None) for fn in found]
    return [replace(fn, coverage=tscov.coverage_for_ts_span(file_cov, fn.span)) for fn in found]


def _narrow_public(
    functions: list[Any],
    files: list[Path],
    root: Path,
    entries: Sequence[Path] | None,
    modules: dict[str, Any],
    on_warning: WarnFn,
) -> list[Any]:
    """Narrow `is_public` to entry-barrel reachability (only demotes; never promotes). A non-barrel
    project or an unresolved wildcard leaves flags untouched — the same safety rail the informational
    CLI path uses."""
    resolved_entries = ts.detect_ts_entries(root, files, list(entries or []))
    if not resolved_entries:
        if entries:
            _warn(
                on_warning,
                "public surface: --ts-entry did not match any scanned file; keeping export flags",
            )
        return functions
    if entries and len(resolved_entries) < len(entries):
        unmatched = len(entries) - len(resolved_entries)
        _warn(on_warning, f"public surface: {unmatched} --ts-entry path(s) matched no scanned file")
    result = tsx.resolve_entry_reachable(modules, resolved_entries)
    if result.poison_all:
        _warn(
            on_warning,
            "public surface: an unresolved wildcard re-export (`export *`) or entry means the "
            "surface can't be bounded; keeping file-level export flags",
        )
        return functions
    _warn(
        on_warning,
        f"public surface narrowed to entry {', '.join(resolved_entries)} (override with --ts-entry)",
    )
    narrowed: list[Any] = []
    kept_uncertain = 0
    for fn in functions:
        binding = fn.id.qualname.split(".", 1)[0]
        if fn.is_public and (fn.id.path, binding) not in result.reachable:
            if binding in result.uncertain_names:
                kept_uncertain += 1
            else:
                fn = replace(fn, is_public=False)
        narrowed.append(fn)
    if kept_uncertain:
        _warn(
            on_warning,
            f"public surface: kept {kept_uncertain} function(s) public behind unresolved named re-exports",
        )
    return narrowed


def _is_allowed(path: str, qualname: str, patterns: Sequence[str]) -> bool:
    """Mirror `engine._is_allowed`, sharing its per-pattern dispatch."""
    return any(pattern_matches(p, f"{path}::{qualname}", path, qualname) for p in patterns)


def _count_lines(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _warn(on_warning: WarnFn, message: str) -> None:
    if on_warning is not None:
        on_warning(message)
