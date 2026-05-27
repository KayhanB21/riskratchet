"""Shared fixture builders for reporting tests.

Two flavors of fixture live here:

1. **In-memory model fixtures** (`scan_report`, `regressions_list`,
   `diff_report_full`) build `RiskReport`/`Regression`/`DiffReport`
   objects directly. Use these to exercise renderer functions in
   isolation, e.g. to pin the output of a specific edge case like a
   multi-target `AMBIGUOUS_RENAME` entry.

2. **On-disk CLI fixtures** (`make_cli_project`) build a small Python
   project under `tmp_path` with a hand-crafted `coverage.json` so
   `CliRunner.invoke(app, ["scan", str(src), …])` produces stable
   output. Use these to pin the actual CLI dispatch path.

Both are consumed by `tests/test_reporting_snapshots.py` (the
comprehensive syrupy suite) and `tests/test_reporting.py` (the
existing renderer unit tests).
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

from riskratchet.models import (
    ChurnStats,
    ComplexityStats,
    CoverageStats,
    DiffEntry,
    DiffReport,
    DiffStatus,
    FileStats,
    FunctionId,
    FunctionRisk,
    FunctionSpan,
    Regression,
    RegressionKind,
    RiskComponents,
    RiskReport,
)
from riskratchet.reporting import SourceLinks


def _components(score: float = 50.0) -> RiskComponents:
    return RiskComponents(score, score, score, score, score, score)


def _fn(
    qualname: str = "foo",
    score: float = 50.0,
    *,
    path: str = "m.py",
    cyclomatic: int = 5,
    line_coverage: float = 0.5,
    branch_coverage: float | None = 0.5,
    commits: int = 0,
    start_line: int = 1,
    end_line: int = 10,
    total_lines: int = 100,
    is_public: bool = True,
    group: str | None = None,
    fingerprint: str | None = None,
    signature: str | None = None,
    component_score: float | None = None,
) -> FunctionRisk:
    """Build a FunctionRisk with deterministic defaults.

    `component_score` overrides the score used to fill RiskComponents,
    useful when the per-component values should diverge from the
    overall `score` (e.g. for cosine-similarity tests).
    """
    components_value = score if component_score is None else component_score
    return FunctionRisk(
        id=FunctionId(path=path, qualname=qualname),
        span=FunctionSpan(start_line=start_line, end_line=end_line),
        is_public=is_public,
        complexity=ComplexityStats(cyclomatic=cyclomatic),
        coverage=CoverageStats(line_coverage=line_coverage, branch_coverage=branch_coverage),
        churn=ChurnStats(commits=commits),
        file_stats=FileStats(path=path, total_lines=total_lines, function_count=1),
        components=_components(components_value),
        score=score,
        crap=10.0,
        group=group,
        fingerprint=fingerprint,
        signature=signature,
    )


def _report(*fns: FunctionRisk, **overrides: object) -> RiskReport:
    """Minimal RiskReport for the existing tests/test_reporting.py call sites."""
    return RiskReport(
        functions=fns,
        files=(FileStats(path="m.py", total_lines=100, function_count=len(fns)),),
        **overrides,  # type: ignore[arg-type]
    )


def scan_report() -> RiskReport:
    """Multi-severity scan fixture exercising styling branches.

    Includes:
    - 1 critical-severity function
    - 1 high-severity function
    - 1 medium-severity function
    - 1 low-severity function
    - 1 function with branch_coverage=None
    - 1 grouped function
    - suppressed_functions=2, skipped_missing_coverage=1
    - coverage_status="present"
    """
    return RiskReport(
        functions=(
            _fn("crit", 90.0, path="a.py", cyclomatic=20, line_coverage=0.0, commits=20),
            _fn("hi", 70.0, path="a.py", cyclomatic=10),
            _fn("med", 45.0, path="a.py", cyclomatic=6),
            _fn("low", 10.0, path="a.py"),
            _fn("nobranch", 30.0, path="b.py", branch_coverage=None),
            _fn("inside_group", 55.0, path="c.py", group="api"),
        ),
        files=(
            FileStats(path="a.py", total_lines=200, function_count=4),
            FileStats(path="b.py", total_lines=80, function_count=1),
            FileStats(path="c.py", total_lines=60, function_count=1),
        ),
        coverage_status="present",
        suppressed_functions=2,
        skipped_missing_coverage=1,
        analyzed_functions=9,
    )


def scan_report_with_overflow() -> RiskReport:
    """30 low-priority functions to trip the >20 overflow branches in PR comments."""
    return RiskReport(
        functions=tuple(_fn(f"f{i:02d}", 25.0 + (i * 0.1), path="big.py") for i in range(30)),
        files=(FileStats(path="big.py", total_lines=500, function_count=30),),
        coverage_status="present",
        analyzed_functions=30,
    )


def regressions_list() -> list[Regression]:
    """One Regression of each RegressionKind value, including a current=None case."""
    crit = _fn("crit", 90.0, path="a.py")
    grew = _fn("grew", 75.0, path="a.py")
    existing = _fn("existing", 60.0, path="a.py")
    component = _fn("component_grew", 50.0, path="b.py")
    return [
        Regression(
            id=crit.id,
            kind=RegressionKind.NEW_ABOVE_THRESHOLD,
            current_score=90.0,
            previous_score=None,
            delta=None,
            reason="function is absent from baseline with score 90.0; exceeds new-function threshold 50.0",
            current=crit,
        ),
        Regression(
            id=grew.id,
            kind=RegressionKind.REGRESSED,
            current_score=75.0,
            previous_score=50.0,
            delta=25.0,
            reason="risk grew by +25.0 (from 50.0 to 75.0); tolerance is +5.0",
            current=grew,
        ),
        Regression(
            id=existing.id,
            kind=RegressionKind.EXISTING_ABOVE_THRESHOLD,
            current_score=60.0,
            previous_score=60.0,
            delta=0.0,
            reason="function score 60.0 exceeds fail-above threshold 50.0",
            current=existing,
        ),
        Regression(
            id=component.id,
            kind=RegressionKind.COMPONENT_REGRESSED,
            current_score=50.0,
            previous_score=50.0,
            delta=0.0,
            reason="component coverage_gap grew by +30.0 (from 20.0 to 50.0); tolerance is +10.0",
            current=component,
        ),
    ]


def diff_report_full() -> DiffReport:
    """One DiffEntry of each DiffStatus + a multi-target AMBIGUOUS_RENAME."""
    fn_reg = _fn("regressed", 60.0, path="a.py")
    fn_comp = _fn("comp_regressed", 50.0, path="a.py")
    fn_new = _fn("new", 30.0, path="a.py")
    fn_imp = _fn("improved", 40.0, path="a.py")
    fn_moved = _fn("moved", 20.0, path="new.py")
    fn_amb = _fn("renamed", 55.0, path="a.py")
    return DiffReport(
        entries=(
            DiffEntry(
                id=fn_reg.id,
                status=DiffStatus.REGRESSED,
                current_score=60.0,
                previous_score=40.0,
                delta=20.0,
                current=fn_reg,
                reason="risk grew by +20.0 (from 40.0 to 60.0); tolerance is +5.0",
            ),
            DiffEntry(
                id=fn_comp.id,
                status=DiffStatus.COMPONENT_REGRESSED,
                current_score=50.0,
                previous_score=50.0,
                delta=0.0,
                current=fn_comp,
                reason="component coverage_gap grew by +20.0",
            ),
            DiffEntry(
                id=fn_imp.id,
                status=DiffStatus.IMPROVED,
                current_score=40.0,
                previous_score=80.0,
                delta=-40.0,
                current=fn_imp,
                reason="risk improved by -40.0 (from 80.0 to 40.0)",
            ),
            DiffEntry(
                id=fn_new.id,
                status=DiffStatus.NEW,
                current_score=30.0,
                previous_score=None,
                delta=None,
                current=fn_new,
                reason="function is absent from baseline with score 30.0",
            ),
            DiffEntry(
                id=FunctionId("a.py", "removed"),
                status=DiffStatus.REMOVED,
                current_score=None,
                previous_score=30.0,
                delta=None,
                reason="removed function from baseline with score 30.0",
            ),
            DiffEntry(
                id=fn_moved.id,
                status=DiffStatus.MOVED,
                current_score=20.0,
                previous_score=20.0,
                delta=0.0,
                current=fn_moved,
                previous_id=FunctionId("old.py", "moved"),
                reason="moved from old.py::moved with no score regression",
            ),
            DiffEntry(
                id=fn_amb.id,
                status=DiffStatus.AMBIGUOUS_RENAME,
                current_score=55.0,
                previous_score=None,
                delta=None,
                current=fn_amb,
                reason="rename ambiguous: 2 baseline candidates score within the ambiguity band",
                previous_targets=(
                    FunctionId("a.py", "candidate_one"),
                    FunctionId("a.py", "candidate_two"),
                ),
                match_confidence=0.71,
            ),
            DiffEntry(
                id=FunctionId("a.py", "unchanged"),
                status=DiffStatus.UNCHANGED,
                current_score=20.0,
                previous_score=20.0,
                delta=0.0,
                reason="risk unchanged at 20.0",
            ),
        )
    )


def links() -> SourceLinks:
    return SourceLinks(repo_url="https://github.com/acme/project", commit_ref="abc1234")


_CLI_FIXTURE_SOURCE = (
    dedent(
        """
    def trivial():
        return 1


    def branchy(x):
        if x > 0:
            if x > 10:
                return "big"
            return "small"
        if x < 0:
            return "negative"
        return "zero"


    def huge():
        # 40-line uncovered function; high sprawl + coverage_gap.
        a = 1
        b = 2
        c = 3
        d = 4
        e = 5
        f = 6
        g = 7
        h = 8
        i = 9
        j = 10
        k = 11
        l = 12
        m = 13
        n = 14
        o = 15
        p = 16
        q = 17
        r = 18
        s = 19
        t = 20
        u = 21
        v = 22
        w = 23
        x = 24
        y = 25
        z = 26
        return (
            a + b + c + d + e + f + g + h + i + j + k + l + m
            + n + o + p + q + r + s + t + u + v + w + x + y + z
        )
    """
    ).strip()
    + "\n"
)


_CLI_FIXTURE_COVERAGE = {
    "files": {
        "src/m.py": {
            "executed_lines": [1, 2, 5, 6, 7, 8, 9, 10, 11, 12],
            "missing_lines": list(range(15, 60)),
            "summary": {
                "covered_lines": 10,
                "num_statements": 55,
                "percent_covered": 18.0,
                "percent_covered_display": "18",
                "missing_lines": 45,
                "excluded_lines": 0,
                "num_branches": 4,
                "num_partial_branches": 0,
                "covered_branches": 2,
                "missing_branches": 2,
            },
        }
    },
    "totals": {
        "covered_lines": 10,
        "num_statements": 55,
        "percent_covered": 18.0,
        "missing_lines": 45,
        "excluded_lines": 0,
        "num_branches": 4,
        "num_partial_branches": 0,
        "covered_branches": 2,
        "missing_branches": 2,
    },
}


def make_cli_project(tmp_path: Path) -> tuple[Path, Path]:
    """Build a minimal on-disk project for CliRunner-based tests.

    Returns `(src_path, coverage_path)`. Drops a `pyproject.toml` next
    to `src/` so riskratchet's project-root detection anchors paths
    as project-relative — otherwise the absolute tmp_path leaks into
    the rendered output and snapshots become non-deterministic.
    """
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "snapshot-fixture"\nversion = "0.0.0"\n',
        encoding="utf-8",
    )
    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "m.py").write_text(_CLI_FIXTURE_SOURCE, encoding="utf-8")
    coverage = tmp_path / "coverage.json"
    coverage.write_text(json.dumps(_CLI_FIXTURE_COVERAGE, indent=2), encoding="utf-8")
    return src, coverage
