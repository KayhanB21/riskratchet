"""Tests for the rendering layer.

Each renderer is exercised twice: once with content (non-empty report or
regressions) and once with the empty case, so the no-content branches stay
covered.
"""

from __future__ import annotations

import json

from reporting_fixtures import _fn, _report
from riskratchet.models import (
    DiffEntry,
    DiffReport,
    DiffStatus,
    FunctionId,
    Regression,
    RegressionKind,
    Severity,
)
from riskratchet.reporting import (
    SourceLinks,
    _sarif_level_for_severity,
    render_function_explanation,
    render_regressions_json,
    render_regressions_markdown,
    render_regressions_pr_comment,
    render_regressions_sarif,
    render_regressions_summary_json,
    render_regressions_summary_text,
    render_regressions_table,
    render_report_json,
    render_report_markdown,
    render_report_pr_comment,
    render_report_sarif,
    render_report_table,
)


def test_render_report_table_renders_rows_and_summary() -> None:
    out = render_report_table(_report(_fn("a", 80.0), _fn("b", 10.0)))
    assert "riskratchet scan" in out
    assert "a" in out and "b" in out
    assert "Summary" in out


def test_render_report_table_hides_overflow_with_limit() -> None:
    fns = tuple(_fn(f"n{i}", float(i)) for i in range(5))
    out = render_report_table(_report(*fns), limit=2)
    assert "more functions hidden" in out


def test_render_report_json_is_valid_json_with_summary() -> None:
    payload = json.loads(render_report_json(_report(_fn("a", 80.0))))
    assert "summary" in payload
    assert payload["summary"]["total_functions"] == 1
    assert isinstance(payload["functions"], list)
    assert payload["functions"][0]["qualname"] == "a"


def test_render_report_markdown_emits_table_and_overflow_note() -> None:
    fns = tuple(_fn(f"n{i}", float(i)) for i in range(5))
    out = render_report_markdown(_report(*fns), limit=2)
    assert "# riskratchet report" in out
    assert "| Severity |" in out
    assert "more functions hidden" in out


def test_render_report_markdown_handles_missing_branch_coverage() -> None:
    out = render_report_markdown(_report(_fn(branch_coverage=None)))
    assert "n/a" in out


def test_render_report_pr_comment_prioritizes_high_risk_and_collapses_lower_priority() -> None:
    out = render_report_pr_comment(_report(_fn("high", 80.0), _fn("medium", 45.0), _fn("low", 5.0)), limit=1)

    assert out.startswith("<!-- riskratchet-report -->\n# riskratchet\n")
    assert "| critical | 80.0 |" in out
    assert "`m.py::high`" in out
    assert "<details><summary>Lower-priority findings (2)</summary>" in out
    assert "`m.py::medium`" in out
    assert "`m.py::low`" in out


def test_render_report_sarif_filters_low_risk_functions_by_default() -> None:
    payload = json.loads(render_report_sarif(_report(_fn("a", 80.0), _fn("b", 10.0))))
    assert payload["version"] == "2.1.0"
    assert payload["$schema"] == "https://json.schemastore.org/sarif-2.1.0.json"
    run = payload["runs"][0]
    assert run["tool"]["driver"]["name"] == "riskratchet"
    assert len(run["tool"]["driver"]["rules"]) >= 1
    results = run["results"]
    assert len(results) == 1
    assert {result["ruleId"] for result in results} == {"riskratchet.function-risk"}
    assert {result["properties"]["qualname"] for result in results} == {"a"}
    assert results[0]["locations"][0]["physicalLocation"]["region"] == {"startLine": 1, "endLine": 10}


def test_render_regressions_table_empty_and_populated() -> None:
    assert "No risk regressions" in render_regressions_table([])
    regression = Regression(
        id=FunctionId("m.py", "foo"),
        kind=RegressionKind.REGRESSED,
        current_score=70.0,
        previous_score=50.0,
        delta=20.0,
        reason="risk grew",
    )
    out = render_regressions_table([regression])
    assert "regressions" in out
    assert "foo" in out


def test_render_regressions_table_does_not_truncate_function_target() -> None:
    target = "src/riskratchet/_version.py::_local_pyproject_version"
    regression = Regression(
        id=FunctionId("src/riskratchet/_version.py", "_local_pyproject_version"),
        kind=RegressionKind.NEW_ABOVE_THRESHOLD,
        current_score=51.0,
        previous_score=None,
        delta=None,
        reason="function is absent from baseline with score 51.0; exceeds new-function threshold 50.0",
    )

    out = render_regressions_table([regression])

    assert target in out
    assert "::_lo…" not in out


def test_render_regressions_json_emits_payload() -> None:
    regression = Regression(
        id=FunctionId("m.py", "foo"),
        kind=RegressionKind.NEW_ABOVE_THRESHOLD,
        current_score=70.0,
        previous_score=None,
        delta=None,
        reason="new risky function",
    )
    payload = json.loads(render_regressions_json([regression]))
    assert payload["regressions"][0]["qualname"] == "foo"


def test_render_regressions_markdown_empty_and_populated() -> None:
    assert "No risk regressions" in render_regressions_markdown([])
    regression = Regression(
        id=FunctionId("m.py", "foo"),
        kind=RegressionKind.REGRESSED,
        current_score=70.0,
        previous_score=50.0,
        delta=20.0,
        reason="risk grew",
    )
    out = render_regressions_markdown([regression])
    assert "# riskratchet regressions" in out
    assert "| Kind |" in out
    assert "foo" in out


def test_render_regressions_markdown_links_current_when_available() -> None:
    fn = _fn("foo", 70.0)
    linked = Regression(
        id=fn.id,
        kind=RegressionKind.REGRESSED,
        current_score=70.0,
        previous_score=50.0,
        delta=20.0,
        reason="risk grew",
        current=fn,
    )
    unlinked = Regression(
        id=FunctionId("m.py", "gone"),
        kind=RegressionKind.NEW_ABOVE_THRESHOLD,
        current_score=60.0,
        previous_score=None,
        delta=None,
        reason="new risky function",
    )
    out = render_regressions_markdown(
        [linked, unlinked],
        links=SourceLinks(repo_url="https://github.com/acme/project", commit_ref="abc123"),
    )
    assert "[`m.py::foo`](https://github.com/acme/project/blob/abc123/m.py#L1-L10)" in out
    assert "| new_above_threshold | `m.py::gone` |" in out


def test_render_regressions_pr_comment_empty_and_populated_with_links() -> None:
    empty = render_regressions_pr_comment([])
    # P8 (since 0.2.8): the regressions PR comment now carries a one-line
    # summary block for parity with scan/diff PR comments.
    assert empty.startswith("<!-- riskratchet-report -->\n# riskratchet\n\n**Regressions:** 0 ")
    assert "_No risk regressions detected._" in empty
    fn = _fn("foo", 70.0)
    regression = Regression(
        id=fn.id,
        kind=RegressionKind.REGRESSED,
        current_score=70.0,
        previous_score=50.0,
        delta=20.0,
        reason="risk grew",
        current=fn,
    )
    out = render_regressions_pr_comment(
        [regression],
        links=SourceLinks(repo_url="https://github.com/acme/project", commit_ref="abc123"),
    )
    assert out.startswith("<!-- riskratchet-report -->\n# riskratchet\n")
    assert "**Regressions:** 1" in out
    assert "**Regressed:** 1" in out
    assert "| Kind | Function | Before | After | Delta | Reason |" in out
    assert "[`m.py::foo`](https://github.com/acme/project/blob/abc123/m.py#L1-L10)" in out


def test_render_regressions_summary_text_counts_kinds_groups_and_diff() -> None:
    fn = _fn("foo", 70.0, group="core")
    regression = Regression(
        id=fn.id,
        kind=RegressionKind.REGRESSED,
        current_score=70.0,
        previous_score=50.0,
        delta=20.0,
        reason="risk grew",
        current=fn,
    )
    diff_report = DiffReport(
        entries=(
            DiffEntry(
                id=fn.id,
                status=DiffStatus.REGRESSED,
                current_score=70.0,
                previous_score=50.0,
                delta=20.0,
                current=fn,
                group="core",
                reason="risk grew",
            ),
            DiffEntry(
                id=FunctionId("m.py", "new"),
                status=DiffStatus.NEW,
                current_score=30.0,
                previous_score=None,
                delta=None,
                current=_fn("new", 30.0),
                reason="new function",
            ),
        )
    )
    out = render_regressions_summary_text([regression], diff_report=diff_report)
    assert out.startswith(
        "check regressions=1 new_above_threshold=0 regressed=1 "
        "existing_above_threshold=0 component_regressed=0 above_threshold=0\n"
    )
    assert (
        "diff regressed=1 component_regressed=0 improved=0 new=1 "
        "ambiguous_rename=0 removed=0 moved=0 unchanged=0"
    ) in out
    assert (
        "group name=core above_threshold=0 component_regressed=0 existing_above_threshold=0 "
        "new_above_threshold=0 regressed=1" in out
    )

    clean_out = render_regressions_summary_text([], diff_report=diff_report)
    assert (
        "group name=core ambiguous_rename=0 component_regressed=0 improved=0 moved=0 "
        "new=0 regressed=1 removed=0 unchanged=0" in clean_out
    )


def test_render_regressions_summary_json_uses_summary_envelope() -> None:
    payload = json.loads(render_regressions_summary_json([]))
    assert payload["command"] == "check"
    assert payload["summary"]["regressions"] == 0
    assert payload["summary"]["by_kind"] == {
        "new_above_threshold": 0,
        "regressed": 0,
        "existing_above_threshold": 0,
        "component_regressed": 0,
        "above_threshold": 0,
    }


def test_render_regressions_sarif_includes_location_and_message() -> None:
    fn = _fn("foo", 70.0)
    regression = Regression(
        id=fn.id,
        kind=RegressionKind.REGRESSED,
        current_score=70.0,
        previous_score=50.0,
        delta=20.0,
        reason="risk grew",
        current=fn,
    )
    payload = json.loads(render_regressions_sarif([regression]))
    result = payload["runs"][0]["results"][0]
    assert result["ruleId"] == "riskratchet.regression"
    assert result["level"] == "warning"
    assert "risk grew" in result["message"]["text"]
    assert result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "m.py"
    assert result["locations"][0]["physicalLocation"]["region"] == {"startLine": 1, "endLine": 10}
    assert result["properties"]["components"]["coverage_gap"] == 70.0
    assert result["properties"]["is_public"] is True


def test_sarif_severity_to_level_mapping() -> None:
    assert _sarif_level_for_severity(Severity.LOW) == "note"
    assert _sarif_level_for_severity(Severity.MEDIUM) == "warning"
    assert _sarif_level_for_severity(Severity.HIGH) == "warning"
    assert _sarif_level_for_severity(Severity.CRITICAL) == "error"


def test_render_function_explanation_includes_all_signals() -> None:
    out = render_function_explanation(_fn("foo", 80.0, cyclomatic=15, commits=8))
    assert "foo" in out
    assert "severity" in out
    assert "complexity" in out
    assert "components" in out
    assert "remediation" in out


def test_remediation_low_risk_message() -> None:
    out = render_function_explanation(_fn("foo", 5.0, cyclomatic=1, line_coverage=1.0, branch_coverage=1.0))
    assert "within tolerance" in out


def test_remediation_lists_triggers_for_high_risk() -> None:
    out = render_function_explanation(
        _fn(
            "foo",
            90.0,
            cyclomatic=20,
            line_coverage=0.0,
            branch_coverage=0.0,
            commits=20,
            end_line=200,
            total_lines=2000,
        )
    )
    assert "line coverage" in out
    assert "branch coverage" in out
    assert "cyclomatic complexity" in out
    assert "recent commits" in out
    assert "spans" in out
