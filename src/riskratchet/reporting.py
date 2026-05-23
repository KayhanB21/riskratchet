"""Renderers for RiskReport and Regression lists.

Four formats are supported: a rich-rendered table for terminals, JSON for
scripts and snapshot tests, markdown for PR comments, and SARIF for code
scanning systems. Each format has a function for the full risk report and a
function for a regressions list; the surfaces are kept symmetric so the CLI
can pick either uniformly.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from io import StringIO
from typing import Any

from rich.console import Console
from rich.table import Table

from riskratchet.models import FunctionRisk, Regression, RiskReport, Severity
from riskratchet.scoring import severity

_SEVERITY_STYLE: dict[Severity, str] = {
    Severity.LOW: "green",
    Severity.MEDIUM: "yellow",
    Severity.HIGH: "red",
    Severity.CRITICAL: "bold red",
}


def render_report_table(report: RiskReport, *, limit: int | None = 20, include_summary: bool = True) -> str:
    sorted_fns = _sorted_by_risk(report.functions)
    displayed = sorted_fns if limit is None else sorted_fns[:limit]

    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=120)
    table = Table(title="riskratchet scan", show_header=True, header_style="bold")
    table.add_column("Sev")
    table.add_column("Score", justify="right")
    table.add_column("CRAP", justify="right")
    table.add_column("CC", justify="right")
    table.add_column("LCov %", justify="right")
    table.add_column("BCov %", justify="right")
    table.add_column("Function")
    table.add_column("Lines", justify="right")

    for fn in displayed:
        sev = severity(fn.score)
        table.add_row(
            f"[{_SEVERITY_STYLE[sev]}]{sev.value}[/]",
            f"{fn.score:.1f}",
            f"{fn.crap:.1f}",
            str(fn.complexity.cyclomatic),
            f"{fn.coverage.line_coverage * 100:.0f}",
            _branch_cell(fn),
            fn.id.as_target(),
            f"{fn.span.start_line}-{fn.span.end_line}",
        )
    console.print(table)
    if limit is not None and len(sorted_fns) > limit:
        console.print(f"... {len(sorted_fns) - limit} more functions hidden (use --limit to show more)")
    if include_summary:
        console.print(_summary_line(report))
        if report.coverage_status == "missing":
            console.print("Coverage: missing (all functions are treated as uncovered).")
    return buf.getvalue()


def render_report_json(report: RiskReport) -> str:
    payload: dict[str, Any] = {
        "summary": _summary_payload(report),
        "functions": [_function_payload(fn) for fn in _sorted_by_risk(report.functions)],
    }
    return json.dumps(payload, indent=2) + "\n"


def render_report_markdown(report: RiskReport, *, limit: int | None = 20) -> str:
    sorted_fns = _sorted_by_risk(report.functions)
    displayed = sorted_fns if limit is None else sorted_fns[:limit]
    lines = [
        "# riskratchet report",
        "",
        f"**Functions analyzed:** {len(report.functions)}",
        f"**Files analyzed:** {len(report.files)}",
        f"**Coverage:** {report.coverage_status}",
        "",
        "| Severity | Score | CRAP | CC | LCov | BCov | Function | Lines |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |",
    ]
    for fn in displayed:
        lines.append(_markdown_row(fn))
    if limit is not None and len(sorted_fns) > limit:
        lines.append("")
        lines.append(f"_... {len(sorted_fns) - limit} more functions hidden._")
    return "\n".join(lines) + "\n"


def render_report_sarif(report: RiskReport) -> str:
    results = [_function_sarif_result(fn) for fn in _sorted_by_risk(report.functions)]
    return json.dumps(_sarif_log(results), indent=2) + "\n"


def render_regressions_table(regressions: list[Regression]) -> str:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=120)
    if not regressions:
        console.print("[green]No risk regressions detected.[/]")
        return buf.getvalue()
    table = Table(title="riskratchet regressions", show_header=True, header_style="bold red")
    table.add_column("Kind")
    table.add_column("Function")
    table.add_column("Before", justify="right")
    table.add_column("After", justify="right")
    table.add_column("Delta", justify="right")
    table.add_column("Reason")
    for reg in regressions:
        table.add_row(
            reg.kind.value,
            reg.id.as_target(),
            _fmt_optional(reg.previous_score),
            f"{reg.current_score:.1f}",
            _fmt_optional(reg.delta, signed=True),
            reg.reason,
        )
    console.print(table)
    return buf.getvalue()


def render_regressions_json(regressions: list[Regression]) -> str:
    payload = [
        {
            "path": reg.id.path,
            "qualname": reg.id.qualname,
            "kind": reg.kind.value,
            "current_score": reg.current_score,
            "previous_score": reg.previous_score,
            "delta": reg.delta,
            "reason": reg.reason,
        }
        for reg in regressions
    ]
    return json.dumps({"regressions": payload}, indent=2) + "\n"


def render_regressions_markdown(regressions: list[Regression]) -> str:
    if not regressions:
        return "_No risk regressions detected._\n"
    lines = [
        "# riskratchet regressions",
        "",
        "| Kind | Function | Before | After | Delta | Reason |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for reg in regressions:
        lines.append(_regression_markdown_row(reg))
    return "\n".join(lines) + "\n"


def render_regressions_sarif(regressions: list[Regression]) -> str:
    return json.dumps(_sarif_log([_regression_sarif_result(reg) for reg in regressions]), indent=2) + "\n"


def render_function_explanation(fn: FunctionRisk) -> str:
    """Verbose, human-readable explanation for `explain` command."""
    sev = severity(fn.score)
    lines = [
        f"{fn.id.as_target()}",
        f"  severity     : {sev.value}",
        f"  score        : {fn.score:.1f}",
        f"  crap         : {fn.crap:.1f}",
        f"  complexity   : CC={fn.complexity.cyclomatic}",
        f"  coverage     : line={fn.coverage.line_coverage * 100:.0f}%, "
        f"branch={_branch_pct(fn.coverage.branch_coverage)}",
        f"  churn        : {fn.churn.commits} commits in window",
        f"  public       : {fn.is_public}",
        f"  lines        : {fn.span.start_line}-{fn.span.end_line} "
        f"(function {fn.span.line_count} lines, file {fn.file_stats.total_lines})",
        "  components   :",
        f"    coverage_gap          {fn.components.coverage_gap:.1f}",
        f"    structural_complexity {fn.components.structural_complexity:.1f}",
        f"    branch_gap            {fn.components.branch_gap:.1f}",
        f"    churn                 {fn.components.churn:.1f}",
        f"    public_surface        {fn.components.public_surface:.1f}",
        f"    sprawl                {fn.components.sprawl:.1f}",
        "",
        _remediation(fn),
    ]
    return "\n".join(lines) + "\n"


def _remediation(fn: FunctionRisk) -> str:
    triggers = []
    if fn.components.coverage_gap >= 50:
        triggers.append(f"line coverage at {fn.coverage.line_coverage * 100:.0f}%")
    if fn.components.branch_gap >= 50 and fn.coverage.branch_coverage is not None:
        triggers.append(f"branch coverage at {fn.coverage.branch_coverage * 100:.0f}%")
    if fn.components.structural_complexity >= 50:
        triggers.append(f"cyclomatic complexity={fn.complexity.cyclomatic}")
    if fn.components.churn >= 50:
        triggers.append(f"{fn.churn.commits} recent commits touch this file")
    if fn.components.sprawl >= 50:
        triggers.append(
            f"function spans {fn.span.line_count} lines in a {fn.file_stats.total_lines}-line file"
        )
    if not triggers:
        return "  remediation : risk is within tolerance."
    advice = "Add tests for missing branches or split this function before changing it further."
    return "  remediation : " + "; ".join(triggers) + ".\n                " + advice


def _markdown_row(fn: FunctionRisk) -> str:
    cells = [
        severity(fn.score).value,
        f"{fn.score:.1f}",
        f"{fn.crap:.1f}",
        str(fn.complexity.cyclomatic),
        f"{round(fn.coverage.line_coverage * 100)}%",
        _branch_markdown(fn),
        f"`{fn.id.as_target()}`",
        f"{fn.span.start_line}-{fn.span.end_line}",
    ]
    return "| " + " | ".join(cells) + " |"


def _regression_markdown_row(reg: Regression) -> str:
    cells = [
        reg.kind.value,
        f"`{reg.id.as_target()}`",
        _fmt_optional(reg.previous_score),
        f"{reg.current_score:.1f}",
        _fmt_optional(reg.delta, signed=True),
        reg.reason,
    ]
    return "| " + " | ".join(cells) + " |"


def _sorted_by_risk(functions: Iterable[FunctionRisk]) -> list[FunctionRisk]:
    return sorted(functions, key=lambda fn: (-fn.score, fn.id.as_target()))


def _summary_payload(report: RiskReport) -> dict[str, Any]:
    counts: dict[str, int] = {sev.value: 0 for sev in Severity}
    for fn in report.functions:
        counts[severity(fn.score).value] += 1
    return {
        "total_functions": len(report.functions),
        "total_files": len(report.files),
        "coverage_status": report.coverage_status,
        "by_severity": counts,
    }


def _sarif_log(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "riskratchet",
                        "informationUri": "https://github.com/KayhanB21/riskratchet",
                        "rules": [
                            {
                                "id": "riskratchet.function-risk",
                                "name": "Function maintainability risk",
                                "shortDescription": {"text": "Function-level maintainability risk score."},
                                "helpUri": "https://github.com/KayhanB21/riskratchet",
                            },
                            {
                                "id": "riskratchet.regression",
                                "name": "Risk regression",
                                "shortDescription": {
                                    "text": "Function risk increased beyond the configured ratchet."
                                },
                                "helpUri": "https://github.com/KayhanB21/riskratchet",
                            },
                        ],
                    }
                },
                "results": results,
            }
        ],
    }


def _function_sarif_result(fn: FunctionRisk) -> dict[str, Any]:
    sev = severity(fn.score)
    return {
        "ruleId": "riskratchet.function-risk",
        "level": _sarif_level_for_severity(sev),
        "message": {
            "text": (
                f"{fn.id.as_target()} has {sev.value} risk: score {fn.score:.1f}, "
                f"CRAP {fn.crap:.1f}, line coverage {fn.coverage.line_coverage * 100:.0f}%, "
                f"branch coverage {_branch_pct(fn.coverage.branch_coverage)}, "
                f"complexity {fn.complexity.cyclomatic}, churn {fn.churn.commits} commits."
            )
        },
        "locations": [_sarif_location(fn.id.path, fn.span.start_line, fn.span.end_line)],
        "properties": _sarif_function_properties(fn),
    }


def _regression_sarif_result(reg: Regression) -> dict[str, Any]:
    fn = reg.current
    sev = severity(reg.current_score)
    result: dict[str, Any] = {
        "ruleId": "riskratchet.regression",
        "level": _sarif_level_for_severity(sev),
        "message": {
            "text": (
                f"{reg.id.as_target()} regressed: {reg.reason}. "
                f"Current severity is {sev.value} with score {reg.current_score:.1f}."
            )
        },
        "locations": [_sarif_location(fn.id.path, fn.span.start_line, fn.span.end_line)]
        if fn is not None
        else [_sarif_location(reg.id.path, 1, 1)],
        "properties": {
            "kind": reg.kind.value,
            "current_score": reg.current_score,
            "previous_score": reg.previous_score,
            "delta": reg.delta,
            "reason": reg.reason,
        },
    }
    if fn is not None:
        result["properties"].update(_sarif_function_properties(fn))
    return result


def _sarif_location(path: str, start_line: int, end_line: int) -> dict[str, Any]:
    return {
        "physicalLocation": {
            "artifactLocation": {"uri": _sarif_uri(path)},
            "region": {
                "startLine": start_line,
                "endLine": end_line,
            },
        }
    }


def _sarif_uri(path: str) -> str:
    if os.path.isabs(path):
        return os.path.relpath(path).replace(os.sep, "/")
    return path


def _sarif_function_properties(fn: FunctionRisk) -> dict[str, Any]:
    return {
        "path": fn.id.path,
        "qualname": fn.id.qualname,
        "severity": severity(fn.score).value,
        "score": fn.score,
        "crap": fn.crap,
        "complexity": fn.complexity.cyclomatic,
        "line_coverage": fn.coverage.line_coverage,
        "branch_coverage": fn.coverage.branch_coverage,
        "churn_commits": fn.churn.commits,
        "is_public": fn.is_public,
        "components": {
            "coverage_gap": fn.components.coverage_gap,
            "structural_complexity": fn.components.structural_complexity,
            "branch_gap": fn.components.branch_gap,
            "churn": fn.components.churn,
            "public_surface": fn.components.public_surface,
            "sprawl": fn.components.sprawl,
        },
    }


def _sarif_level_for_severity(sev: Severity) -> str:
    if sev is Severity.CRITICAL:
        return "error"
    if sev in {Severity.MEDIUM, Severity.HIGH}:
        return "warning"
    return "note"


def _function_payload(fn: FunctionRisk) -> dict[str, Any]:
    return {
        "path": fn.id.path,
        "qualname": fn.id.qualname,
        "severity": severity(fn.score).value,
        "score": fn.score,
        "crap": fn.crap,
        "complexity": fn.complexity.cyclomatic,
        "line_coverage": fn.coverage.line_coverage,
        "branch_coverage": fn.coverage.branch_coverage,
        "churn_commits": fn.churn.commits,
        "is_public": fn.is_public,
        "lines": {"start": fn.span.start_line, "end": fn.span.end_line},
        "components": {
            "coverage_gap": fn.components.coverage_gap,
            "structural_complexity": fn.components.structural_complexity,
            "branch_gap": fn.components.branch_gap,
            "churn": fn.components.churn,
            "public_surface": fn.components.public_surface,
            "sprawl": fn.components.sprawl,
        },
    }


def _summary_line(report: RiskReport) -> str:
    counts: dict[Severity, int] = {sev: 0 for sev in Severity}
    for fn in report.functions:
        counts[severity(fn.score)] += 1
    parts = [
        f"{counts[Severity.CRITICAL]} critical",
        f"{counts[Severity.HIGH]} high",
        f"{counts[Severity.MEDIUM]} medium",
        f"{counts[Severity.LOW]} low",
    ]
    return f"Summary: {len(report.functions)} functions across {len(report.files)} files. " + ", ".join(parts)


def _branch_cell(fn: FunctionRisk) -> str:
    if fn.coverage.branch_coverage is None:
        return "n/a"
    return f"{fn.coverage.branch_coverage * 100:.0f}"


def _branch_markdown(fn: FunctionRisk) -> str:
    if fn.coverage.branch_coverage is None:
        return "n/a"
    return f"{round(fn.coverage.branch_coverage * 100)}%"


def _branch_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.0f}%"


def _fmt_optional(value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    if signed:
        return f"{value:+.1f}"
    return f"{value:.1f}"
