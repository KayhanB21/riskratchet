"""Tests for full baseline diff classification."""

from __future__ import annotations

from syrupy.assertion import SnapshotAssertion

from riskratchet.baseline import diff, regressions_from_diff
from riskratchet.models import (
    Baseline,
    BaselineEntry,
    ChurnStats,
    ComplexityStats,
    CoverageStats,
    DiffReport,
    DiffStatus,
    FileStats,
    FunctionId,
    FunctionRisk,
    FunctionSpan,
    RegressionKind,
    RiskComponents,
    RiskReport,
)
from riskratchet.reporting import (
    render_diff_github,
    render_diff_markdown,
    render_diff_pr_comment,
    render_diff_table,
)


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
    assert "absent from baseline" in regressions[0].reason


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


def test_diff_report_regressions_preserves_only_score_and_component_regressions() -> None:
    regressed = _fn("a.py", "score_regressed", 70.0)
    component = _fn("a.py", "component_regressed", 45.0)
    improved = _fn("a.py", "improved", 20.0)
    new = _fn("a.py", "new", 80.0)
    removed_id = FunctionId("a.py", "removed")

    diff_report = diff(
        RiskReport(functions=(regressed, component, improved, new), files=()),
        Baseline(
            version="2",
            entries={
                regressed.id: BaselineEntry(
                    id=regressed.id,
                    score=50.0,
                    components=_components(50.0),
                    fingerprint=regressed.fingerprint,
                ),
                component.id: BaselineEntry(
                    id=component.id,
                    score=45.0,
                    components=RiskComponents(
                        coverage_gap=0.0,
                        structural_complexity=45.0,
                        branch_gap=45.0,
                        churn=45.0,
                        public_surface=45.0,
                        sprawl=45.0,
                    ),
                    fingerprint=component.fingerprint,
                ),
                improved.id: BaselineEntry(
                    id=improved.id,
                    score=60.0,
                    components=_components(60.0),
                    fingerprint=improved.fingerprint,
                ),
                removed_id: BaselineEntry(
                    id=removed_id,
                    score=90.0,
                    components=_components(90.0),
                    fingerprint="removed",
                ),
            },
        ),
        fail_regression_above=5.0,
    )

    regressions = diff_report.regressions()

    assert [reg.kind for reg in regressions] == [
        RegressionKind.REGRESSED,
        RegressionKind.COMPONENT_REGRESSED,
    ]
    assert [reg.id.qualname for reg in regressions] == ["score_regressed", "component_regressed"]
    assert regressions[0].current is regressed
    assert regressions[0].previous_score == 50.0
    assert regressions[0].delta == 20.0
    assert regressions[1].current is component
    assert regressions[1].previous_score == 45.0
    assert regressions[1].delta == 0.0
    assert "coverage_gap grew by +45.0" in regressions[1].reason


def test_diff_renderers_cover_failing_statuses_and_non_regression_statuses() -> None:
    old = Baseline(
        version="2",
        entries={
            FunctionId("a.py", "regressed"): BaselineEntry(
                id=FunctionId("a.py", "regressed"),
                score=40.0,
                components=_components(40.0),
                fingerprint="regressed",
            ),
            FunctionId("a.py", "component_regressed"): BaselineEntry(
                id=FunctionId("a.py", "component_regressed"),
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
            FunctionId("a.py", "improved"): BaselineEntry(
                id=FunctionId("a.py", "improved"),
                score=80.0,
                components=_components(80.0),
                fingerprint="improved",
            ),
            FunctionId("a.py", "unchanged"): BaselineEntry(
                id=FunctionId("a.py", "unchanged"),
                score=20.0,
                components=_components(20.0),
                fingerprint="unchanged",
            ),
        },
    )
    diff_report = diff(
        RiskReport(
            functions=(
                _fn("a.py", "regressed", 60.0, component_score=60.0, fingerprint="regressed"),
                _fn("a.py", "component_regressed", 40.0, component_score=40.0, fingerprint="component"),
                _fn("a.py", "improved", 40.0, component_score=40.0, fingerprint="improved"),
                _fn("a.py", "new", 90.0, component_score=90.0, fingerprint="new"),
                _fn("a.py", "unchanged", 20.0, component_score=20.0, fingerprint="unchanged"),
            ),
            files=(),
        ),
        old,
        fail_regression_above=5.0,
    )

    table = render_diff_table(diff_report)
    markdown = render_diff_markdown(diff_report)
    pr_comment = render_diff_pr_comment(diff_report)
    github = render_diff_github(diff_report)

    assert "riskratchet diff" in table
    assert "component_regressed" in table
    assert "| component_regressed | `a.py::component_regressed` |" in markdown
    assert "<details><summary>Improvements (1)</summary>" in pr_comment
    assert "<details><summary>Unchanged functions (1)</summary>" in pr_comment
    assert "risk grew by +20.0" in github
    assert "coverage_gap grew by +40.0" in github
    assert "absent from baseline with score 90.0" in github
    assert "a.py::improved" not in github


def _ambiguous_diff_report() -> tuple[Baseline, RiskReport, DiffReport]:
    from riskratchet.baseline import diff as diff_baseline

    old = Baseline(
        version="2",
        entries={
            FunctionId("a.py", "one"): BaselineEntry(
                id=FunctionId("a.py", "one"),
                score=20.0,
                components=_components(20.0),
                fingerprint="dup",
            ),
            FunctionId("a.py", "two"): BaselineEntry(
                id=FunctionId("a.py", "two"),
                score=20.0,
                components=_components(20.0),
                fingerprint="dup",
            ),
        },
    )
    report = RiskReport(
        functions=(_fn("a.py", "renamed", 60.0, component_score=60.0, fingerprint="dup"),),
        files=(),
    )
    diff_report: DiffReport = diff_baseline(report, old, fail_regression_above=5.0)
    return old, report, diff_report


def test_render_diff_table_includes_ambiguous_rename_row() -> None:
    _, _, diff_report = _ambiguous_diff_report()
    table = render_diff_table(diff_report)
    assert "ambiguous_rename" in table
    assert "a.py::renamed" in table


def test_render_diff_markdown_includes_ambiguous_rename_row() -> None:
    _, _, diff_report = _ambiguous_diff_report()
    markdown = render_diff_markdown(diff_report)
    assert "| ambiguous_rename | `a.py::renamed` |" in markdown


def test_render_diff_pr_comment_keeps_ambiguous_rename_visible() -> None:
    _, _, diff_report = _ambiguous_diff_report()
    pr_comment = render_diff_pr_comment(diff_report)
    # Ambiguous renames must be in the gating block, not collapsed
    visible_block, _, _collapsed = pr_comment.partition("<details>")
    assert "ambiguous_rename" in visible_block
    assert "a.py::renamed" in visible_block


def test_render_diff_github_emits_ambiguous_rename_warning() -> None:
    _, _, diff_report = _ambiguous_diff_report()
    github = render_diff_github(diff_report)
    assert "ambiguous rename" in github.lower()
    assert "a.py" in github


def test_render_diff_json_includes_ambiguous_rename_payload() -> None:
    import json as _json

    from riskratchet.reporting import render_diff_json

    _, _, diff_report = _ambiguous_diff_report()
    payload = _json.loads(render_diff_json(diff_report))
    [entry] = [e for e in payload["entries"] if e["status"] == "ambiguous_rename"]
    targets = {(t["path"], t["qualname"]) for t in entry["previous_targets"]}
    assert targets == {("a.py", "one"), ("a.py", "two")}
    assert entry["match_confidence"] is not None
    assert payload["summary"]["ambiguous_rename"] == 1


def test_diff_emits_ambiguous_rename_status() -> None:
    """Two baseline entries plausibly map to the same new function → AMBIGUOUS_RENAME."""
    old = Baseline(
        version="2",
        entries={
            FunctionId("a.py", "one"): BaselineEntry(
                id=FunctionId("a.py", "one"),
                score=20.0,
                components=_components(20.0),
                fingerprint="dup",
            ),
            FunctionId("a.py", "two"): BaselineEntry(
                id=FunctionId("a.py", "two"),
                score=20.0,
                components=_components(20.0),
                fingerprint="dup",
            ),
        },
    )
    report = RiskReport(
        functions=(_fn("a.py", "renamed", 60.0, component_score=60.0, fingerprint="dup"),),
        files=(),
    )
    diff_report = diff(report, old, fail_regression_above=5.0)
    statuses = {e.id.as_target(): e.status for e in diff_report.entries}
    assert statuses["a.py::renamed"] is DiffStatus.AMBIGUOUS_RENAME


def test_diff_ambiguous_rename_lists_candidates_in_entry() -> None:
    old = Baseline(
        version="2",
        entries={
            FunctionId("a.py", "one"): BaselineEntry(
                id=FunctionId("a.py", "one"),
                score=20.0,
                components=_components(20.0),
                fingerprint="dup",
            ),
            FunctionId("a.py", "two"): BaselineEntry(
                id=FunctionId("a.py", "two"),
                score=20.0,
                components=_components(20.0),
                fingerprint="dup",
            ),
        },
    )
    report = RiskReport(
        functions=(_fn("a.py", "renamed", 60.0, component_score=60.0, fingerprint="dup"),),
        files=(),
    )
    diff_report = diff(report, old, fail_regression_above=5.0)
    [entry] = [e for e in diff_report.entries if e.status is DiffStatus.AMBIGUOUS_RENAME]
    assert {fid.qualname for fid in entry.previous_targets} == {"one", "two"}
    assert entry.match_confidence is not None
    assert entry.match_confidence >= 0.6


def test_regressions_from_diff_treats_ambiguous_rename_like_new() -> None:
    """An ambiguous rename is always surfaced — score doesn't matter."""
    old = Baseline(
        version="2",
        entries={
            FunctionId("a.py", "one"): BaselineEntry(
                id=FunctionId("a.py", "one"),
                score=20.0,
                components=_components(20.0),
                fingerprint="dup",
            ),
            FunctionId("a.py", "two"): BaselineEntry(
                id=FunctionId("a.py", "two"),
                score=20.0,
                components=_components(20.0),
                fingerprint="dup",
            ),
        },
    )
    report = RiskReport(
        functions=(_fn("a.py", "renamed", 35.0, component_score=35.0, fingerprint="dup"),),
        files=(),
    )
    diff_report = diff(report, old, fail_regression_above=5.0)
    regressions = regressions_from_diff(diff_report, fail_new_above=50.0)
    # Even though score 35.0 < fail_new_above 50.0, ambiguity always surfaces.
    assert len(regressions) == 1
    assert regressions[0].kind == RegressionKind.NEW_ABOVE_THRESHOLD
    assert "ambiguous" in regressions[0].reason.lower()


def test_diff_moved_status_unchanged_for_unique_match() -> None:
    """The unique-body-fingerprint path still produces MOVED, not AMBIGUOUS_RENAME."""
    old = Baseline(
        version="2",
        entries={
            FunctionId("old.py", "fn"): BaselineEntry(
                id=FunctionId("old.py", "fn"),
                score=20.0,
                components=_components(20.0),
                fingerprint="unique-body",
            ),
        },
    )
    report = RiskReport(
        functions=(_fn("new.py", "fn", 20.0, component_score=20.0, fingerprint="unique-body"),),
        files=(),
    )
    diff_report = diff(report, old, fail_regression_above=5.0)
    [entry] = diff_report.entries
    assert entry.status is DiffStatus.MOVED


def test_render_diff_pr_comment_multi_section_snapshot(snapshot: SnapshotAssertion) -> None:
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
    assert rendered == snapshot
