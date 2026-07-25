"""The single report-building orchestration entry every command routes through (0.3.0).

`build_report` runs the always-on Python backend (`engine.analyze`) and, only when `typescript=True`,
merges in the scored TypeScript backend. `scan`/`check`/`diff`/`baseline` call this one function so
multi-language support is wired in exactly once.

**Import isolation.** This module imports zero TypeScript modules at module load — `typescript_engine`
(and, transitively, the lazy tree-sitter import inside `typescript`) is imported *inside* `build_report`
and only when `typescript=True`. So a default Python-only scan never imports the TypeScript backend,
keeping the "no mandatory Node dependency" non-goal mechanical and greppable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from riskratchet.coverage import MissingCoveragePolicy
from riskratchet.engine import analyze
from riskratchet.git import DEFAULT_CHURN_WINDOW_DAYS
from riskratchet.models import RiskReport


def build_report(
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
    typescript: bool = False,
    ts_coverage_paths: Sequence[Path] | None = None,
    ts_entries: Sequence[Path] | None = None,
    on_ts_warning: Any = None,
    on_ts_error: Any = None,
) -> RiskReport:
    """Build one `RiskReport` across enabled backends.

    Always analyzes Python. When `typescript=True`, additionally analyzes TypeScript (with its own
    complexity calibration and optional `ts_coverage_paths` / `ts_entries`) and merges the two — the
    Python functions first, then TypeScript. The Python-only path is byte-for-byte what
    `engine.analyze` produced before this seam existed.
    """
    report = analyze(
        paths,
        root=root,
        coverage_path=coverage_path,
        coverage_map=coverage_map,
        include=include,
        exclude=exclude,
        allow=allow,
        use_git=use_git,
        churn_days=churn_days,
        weights=weights,
        missing_coverage_policy=missing_coverage_policy,
        groups=groups,
    )
    if not typescript:
        return report

    # Lazy: keeps the TypeScript backend (and its tree-sitter import) out of the Python-only path.
    from riskratchet import typescript_engine

    ts_report = typescript_engine.analyze_typescript(
        paths,
        root=root,
        ts_coverage_paths=ts_coverage_paths,
        ts_entries=ts_entries,
        include=include,
        exclude=exclude,
        allow=allow,
        use_git=use_git,
        churn_days=churn_days,
        weights=weights,
        missing_coverage_policy=missing_coverage_policy,
        groups=groups,
        on_warning=on_ts_warning,
        on_error=on_ts_error,
    )
    return typescript_engine.merge_reports(report, ts_report)
