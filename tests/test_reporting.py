"""Tests for the rendering layer.

Each renderer is exercised twice: once with content (non-empty report or
regressions) and once with the empty case, so the no-content branches stay
covered.
"""

from __future__ import annotations

import json

from riskratchet.models import (
    ChurnStats,
    ComplexityStats,
    CoverageStats,
    FileStats,
    FunctionId,
    FunctionRisk,
    FunctionSpan,
    Regression,
    RegressionKind,
    RiskComponents,
    RiskReport,
    Severity,
)
from riskratchet.reporting import (
    _sarif_level_for_severity,
    render_function_explanation,
    render_regressions_json,
    render_regressions_markdown,
    render_regressions_sarif,
    render_regressions_table,
    render_report_json,
    render_report_markdown,
    render_report_sarif,
    render_report_table,
)


def _components(score: float = 50.0) -> RiskComponents:
    return RiskComponents(score, score, score, score, score, score)


def _fn(
    qualname: str = "foo",
    score: float = 50.0,
    *,
    cyclomatic: int = 5,
    line_coverage: float = 0.5,
    branch_coverage: float | None = 0.5,
    commits: int = 0,
    span_lines: int = 10,
    total_lines: int = 100,
    is_public: bool = True,
) -> FunctionRisk:
    path = "m.py"
    return FunctionRisk(
        id=FunctionId(path=path, qualname=qualname),
        span=FunctionSpan(start_line=1, end_line=span_lines),
        is_public=is_public,
        complexity=ComplexityStats(cyclomatic=cyclomatic),
        coverage=CoverageStats(line_coverage=line_coverage, branch_coverage=branch_coverage),
        churn=ChurnStats(commits=commits),
        file_stats=FileStats(path=path, total_lines=total_lines, function_count=1),
        components=_components(score),
        score=score,
        crap=10.0,
    )


def _report(*fns: FunctionRisk) -> RiskReport:
    return RiskReport(
        functions=fns,
        files=(FileStats(path="m.py", total_lines=100, function_count=len(fns)),),
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


def test_render_report_sarif_includes_all_functions() -> None:
    payload = json.loads(render_report_sarif(_report(_fn("a", 80.0), _fn("b", 10.0))))
    assert payload["version"] == "2.1.0"
    assert payload["$schema"] == "https://json.schemastore.org/sarif-2.1.0.json"
    run = payload["runs"][0]
    assert run["tool"]["driver"]["name"] == "riskratchet"
    assert len(run["tool"]["driver"]["rules"]) >= 1
    results = run["results"]
    assert len(results) == 2
    assert {result["ruleId"] for result in results} == {"riskratchet.function-risk"}
    assert {result["properties"]["qualname"] for result in results} == {"a", "b"}
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
            span_lines=200,
            total_lines=2000,
        )
    )
    assert "line coverage" in out
    assert "branch coverage" in out
    assert "cyclomatic complexity" in out
    assert "recent commits" in out
    assert "spans" in out
