"""Tests for full baseline diff classification."""

from __future__ import annotations

from textwrap import dedent

from riskratchet.baseline import diff, regressions_from_diff
from riskratchet.models import (
    Baseline,
    BaselineEntry,
    ChurnStats,
    ComplexityStats,
    CoverageStats,
    DiffStatus,
    FileStats,
    FunctionId,
    FunctionRisk,
    FunctionSpan,
    RegressionKind,
    RiskComponents,
    RiskReport,
)
from riskratchet.reporting import render_diff_pr_comment


def _components(score: float = 50.0) -> RiskComponents:
    return RiskComponents(
        coverage_gap=score,
        structural_complexity=score,
        branch_gap=score,
        churn=score,
        public_surface=score,
        sprawl=score,
    )


def _fn(
    path: str,
    qualname: str,
    score: float = 50.0,
    *,
    component_score: float | None = None,
    fingerprint: str | None = None,
) -> FunctionRisk:
    file_stats = FileStats(path=path, total_lines=100, function_count=1)
    return FunctionRisk(
        id=FunctionId(path=path, qualname=qualname),
        span=FunctionSpan(start_line=1, end_line=10),
        is_public=True,
        complexity=ComplexityStats(cyclomatic=5),
        coverage=CoverageStats(line_coverage=0.5, branch_coverage=0.5),
        churn=ChurnStats(commits=0),
        file_stats=file_stats,
        components=_components(score if component_score is None else component_score),
        score=score,
        crap=10.0,
        fingerprint=fingerprint or f"{path}:{qualname}",
    )


def test_diff_reports_all_statuses() -> None:
    old_regressed = FunctionId("a.py", "regressed")
    old_component = FunctionId("a.py", "component_regressed")
    old_improved = FunctionId("a.py", "improved")
    old_removed = FunctionId("a.py", "removed")
    old_moved = FunctionId("old.py", "moved")
    old_unchanged = FunctionId("a.py", "unchanged")
    old = Baseline(
        version="2",
        entries={
            old_regressed: BaselineEntry(
                id=old_regressed,
                score=40.0,
                components=_components(40.0),
                fingerprint="regressed",
            ),
            old_component: BaselineEntry(
                id=old_component,
                score=40.0,
                components=RiskComponents(
                    coverage_gap=0.0,
                    structural_complexity=40.0,
                    branch_gap=40.0,
                    churn=40.0,
                    public_surface=40.0,
                    sprawl=40.0,
                ),
                fingerprint="component",
            ),
            old_improved: BaselineEntry(
                id=old_improved,
                score=80.0,
                components=_components(80.0),
                fingerprint="improved",
            ),
            old_removed: BaselineEntry(
                id=old_removed,
                score=30.0,
                components=_components(30.0),
                fingerprint="removed",
            ),
            old_moved: BaselineEntry(
                id=old_moved,
                score=20.0,
                components=_components(20.0),
                fingerprint="moved",
            ),
            old_unchanged: BaselineEntry(
                id=old_unchanged,
                score=20.0,
                components=_components(20.0),
                fingerprint="unchanged",
            ),
        },
    )
    report = RiskReport(
        functions=(
            _fn("a.py", "regressed", 60.0, component_score=60.0, fingerprint="regressed"),
            _fn("a.py", "component_regressed", 40.0, component_score=40.0, fingerprint="component"),
            _fn("a.py", "improved", 40.0, component_score=40.0, fingerprint="improved"),
            _fn("a.py", "new", 30.0, component_score=30.0, fingerprint="new"),
            _fn("new.py", "moved", 20.0, component_score=20.0, fingerprint="moved"),
            _fn("a.py", "unchanged", 20.0, component_score=20.0, fingerprint="unchanged"),
        ),
        files=(),
    )
    diff_report = diff(report, old, fail_regression_above=5.0)
    statuses = {entry.id.as_target(): entry.status for entry in diff_report.entries}
    assert statuses["a.py::regressed"] == DiffStatus.REGRESSED
    assert statuses["a.py::component_regressed"] == DiffStatus.COMPONENT_REGRESSED
    assert statuses["a.py::improved"] == DiffStatus.IMPROVED
    assert statuses["a.py::new"] == DiffStatus.NEW
    assert statuses["new.py::moved"] == DiffStatus.MOVED
    assert statuses["a.py::removed"] == DiffStatus.REMOVED
    assert statuses["a.py::unchanged"] == DiffStatus.UNCHANGED


def test_regressions_from_diff_applies_new_threshold() -> None:
    report = RiskReport(functions=(_fn("a.py", "new", 60.0),), files=())
    diff_report = diff(report, Baseline(version="2"), fail_regression_above=5.0)
    regressions = regressions_from_diff(diff_report, fail_new_above=50.0)
    assert len(regressions) == 1
    assert regressions[0].kind == RegressionKind.NEW_ABOVE_THRESHOLD


def test_regressions_from_diff_applies_existing_threshold() -> None:
    fn = _fn("a.py", "current_debt", 60.0)
    old = Baseline(
        version="2",
        entries={
            fn.id: BaselineEntry(
                id=fn.id,
                score=60.0,
                components=fn.components,
                fingerprint=fn.fingerprint,
            )
        },
    )
    diff_report = diff(RiskReport(functions=(fn,), files=()), old, fail_regression_above=5.0)
    regressions = regressions_from_diff(diff_report, fail_new_above=100.0, fail_existing_above=50.0)
    assert len(regressions) == 1
    assert regressions[0].kind == RegressionKind.EXISTING_ABOVE_THRESHOLD


def test_render_diff_pr_comment_multi_section_snapshot() -> None:
    old = Baseline(
        version="2",
        entries={
            FunctionId("a.py", "regressed"): BaselineEntry(
                id=FunctionId("a.py", "regressed"),
                score=40.0,
                components=_components(40.0),
                fingerprint="regressed",
            ),
            FunctionId("a.py", "improved"): BaselineEntry(
                id=FunctionId("a.py", "improved"),
                score=80.0,
                components=_components(80.0),
                fingerprint="improved",
            ),
            FunctionId("old.py", "moved"): BaselineEntry(
                id=FunctionId("old.py", "moved"),
                score=20.0,
                components=_components(20.0),
                fingerprint="moved",
            ),
            FunctionId("a.py", "removed"): BaselineEntry(
                id=FunctionId("a.py", "removed"),
                score=30.0,
                components=_components(30.0),
                fingerprint="removed",
            ),
            FunctionId("a.py", "unchanged"): BaselineEntry(
                id=FunctionId("a.py", "unchanged"),
                score=20.0,
                components=_components(20.0),
                fingerprint="unchanged",
            ),
        },
    )
    report = RiskReport(
        functions=(
            _fn("a.py", "regressed", 60.0, component_score=60.0, fingerprint="regressed"),
            _fn("a.py", "improved", 40.0, component_score=40.0, fingerprint="improved"),
            _fn("a.py", "new", 30.0, component_score=30.0, fingerprint="new"),
            _fn("new.py", "moved", 20.0, component_score=20.0, fingerprint="moved"),
            _fn("a.py", "unchanged", 20.0, component_score=20.0, fingerprint="unchanged"),
        ),
        files=(),
    )
    rendered = render_diff_pr_comment(diff(report, old, fail_regression_above=5.0))
    expected = dedent(
        """
        <!-- riskratchet-report -->
        # riskratchet

        **Regressions:** 1 · **New:** 1 · **Improved:** 1 · **Moved:** 1 · **Removed:** 1

        | Status | Function | Before | After | Delta | Reason |
        | --- | --- | ---: | ---: | ---: | --- |
        | regressed | `a.py::regressed` | 40.0 | 60.0 | +20.0 | risk grew by +20.0 (from 40.0 to 60.0); tolerance is +5.0 |
        | new | `a.py::new` | n/a | 30.0 | n/a | new function with score 30.0 |

        <details><summary>Improvements (1)</summary>

        | Status | Function | Before | After | Delta | Reason |
        | --- | --- | ---: | ---: | ---: | --- |
        | improved | `a.py::improved` | 80.0 | 40.0 | -40.0 | risk improved by -40.0 (from 80.0 to 40.0) |

        </details>

        <details><summary>Moved functions (1)</summary>

        | Status | Function | Before | After | Delta | Reason |
        | --- | --- | ---: | ---: | ---: | --- |
        | moved | `new.py::moved` | 20.0 | 20.0 | +0.0 | moved from old.py::moved with no score regression |

        </details>

        <details><summary>Removed functions (1)</summary>

        | Status | Function | Before | After | Delta | Reason |
        | --- | --- | ---: | ---: | ---: | --- |
        | removed | `a.py::removed` | 30.0 | n/a | n/a | removed function from baseline with score 30.0 |

        </details>

        <details><summary>Unchanged functions (1)</summary>

        | Status | Function | Before | After | Delta | Reason |
        | --- | --- | ---: | ---: | ---: | --- |
        | unchanged | `a.py::unchanged` | 20.0 | 20.0 | +0.0 | risk unchanged at 20.0 |

        </details>
        """
    ).lstrip()
    assert rendered == expected
