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
from dataclasses import dataclass
from io import StringIO
from typing import Any

from rich.console import Console
from rich.table import Table

from riskratchet.models import (
    DiffEntry,
    DiffReport,
    DiffStatus,
    FunctionRisk,
    Regression,
    RegressionKind,
    RiskReport,
    Severity,
)
from riskratchet.scoring import severity

REPORT_SCHEMA_URL = "https://github.com/KayhanB21/riskratchet/schemas/report.schema.json"
REGRESSIONS_SCHEMA_URL = "https://github.com/KayhanB21/riskratchet/schemas/regressions.schema.json"
DIFF_SCHEMA_URL = "https://github.com/KayhanB21/riskratchet/schemas/diff.schema.json"
SUMMARY_SCHEMA_URL = "https://github.com/KayhanB21/riskratchet/schemas/summary.schema.json"
OUTPUT_VERSION = "0.2"
PR_COMMENT_MARKER = "<!-- riskratchet-report -->"


@dataclass(frozen=True, slots=True)
class SourceLinks:
    repo_url: str
    commit_ref: str

    def link_for(self, fn: FunctionRisk) -> str:
        return (
            f"{self.repo_url.rstrip('/')}/blob/{self.commit_ref}/"
            f"{fn.id.path}#L{fn.span.start_line}-L{fn.span.end_line}"
        )


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
        "$schema": REPORT_SCHEMA_URL,
        "version": OUTPUT_VERSION,
        "summary": _summary_payload(report),
        "functions": [_function_payload(fn) for fn in _sorted_by_risk(report.functions)],
    }
    return json.dumps(payload, indent=2) + "\n"


def render_report_summary_json(report: RiskReport) -> str:
    return _summary_envelope("scan", _summary_payload(report))


def render_report_summary_text(report: RiskReport) -> str:
    summary = _summary_payload(report)
    lines = [
        (
            "scan "
            f"functions={summary['total_functions']} "
            f"analyzed={summary['analyzed_functions']} "
            f"emitted={summary['emitted_functions']} "
            f"files={summary['total_files']} "
            f"coverage={summary['coverage_status']} "
            f"suppressed={summary['suppressed_functions']} "
            f"skipped_missing_coverage={summary['skipped_missing_coverage']}"
        ),
        _severity_summary_line(summary["by_severity"]),
    ]
    lines.extend(_group_summary_lines(summary.get("groups", {})))
    return "\n".join(lines) + "\n"


def render_report_markdown(
    report: RiskReport,
    *,
    limit: int | None = 20,
    links: SourceLinks | None = None,
) -> str:
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
        lines.append(_markdown_row(fn, links=links))
    if limit is not None and len(sorted_fns) > limit:
        lines.append("")
        lines.append(f"_... {len(sorted_fns) - limit} more functions hidden._")
    return "\n".join(lines) + "\n"


def render_report_pr_comment(
    report: RiskReport,
    *,
    limit: int | None = 20,
    links: SourceLinks | None = None,
) -> str:
    sorted_fns = _sorted_by_risk(report.functions)
    high_priority = [fn for fn in sorted_fns if severity(fn.score) in {Severity.HIGH, Severity.CRITICAL}]
    if not high_priority:
        high_priority = sorted_fns[: limit or len(sorted_fns)]
    lower_priority = [fn for fn in sorted_fns if fn not in high_priority]
    displayed = high_priority if limit is None else high_priority[:limit]
    lines = [
        PR_COMMENT_MARKER,
        "# riskratchet",
        "",
        _summary_line(report),
        "",
    ]
    if displayed:
        lines.extend(
            [
                "| Severity | Score | CRAP | CC | LCov | BCov | Group | Function | Lines |",
                "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: |",
            ]
        )
        lines.extend(_markdown_row(fn, links=links, include_group=True) for fn in displayed)
    else:
        lines.append("_No functions emitted._")
    hidden_high_priority = high_priority[len(displayed) :]
    collapsed = hidden_high_priority + lower_priority
    if collapsed:
        lines.extend(["", f"<details><summary>Lower-priority findings ({len(collapsed)})</summary>", ""])
        lines.extend(
            [
                "| Severity | Score | CRAP | CC | LCov | BCov | Group | Function | Lines |",
                "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: |",
            ]
        )
        lines.extend(_markdown_row(fn, links=links, include_group=True) for fn in collapsed[:20])
        if len(collapsed) > 20:
            lines.append(f"_... {len(collapsed) - 20} more hidden._")
        lines.extend(["", "</details>"])
    return "\n".join(lines) + "\n"


def render_report_sarif(report: RiskReport, *, min_score: float = 25.0) -> str:
    results = [
        _function_sarif_result(fn) for fn in _sorted_by_risk(report.functions) if fn.score >= min_score
    ]
    return json.dumps(_sarif_log(results), indent=2) + "\n"


def render_report_github(report: RiskReport, *, min_score: float = 25.0) -> str:
    lines = [_github_annotation(fn) for fn in _sorted_by_risk(report.functions) if fn.score >= min_score]
    return "\n".join(lines) + ("\n" if lines else "")


def render_regressions_table(regressions: list[Regression]) -> str:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=120)
    if not regressions:
        console.print("[green]No risk regressions detected.[/]")
        return buf.getvalue()
    table = Table(title="riskratchet regressions", show_header=True, header_style="bold red")
    table.add_column("Kind")
    table.add_column("Function", no_wrap=True)
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
    return (
        json.dumps(
            {
                "$schema": REGRESSIONS_SCHEMA_URL,
                "version": OUTPUT_VERSION,
                "regressions": [_regression_payload(reg) for reg in regressions],
            },
            indent=2,
        )
        + "\n"
    )


def render_regressions_markdown(regressions: list[Regression], *, links: SourceLinks | None = None) -> str:
    if not regressions:
        return "_No risk regressions detected._\n"
    lines = [
        "# riskratchet regressions",
        "",
        "| Kind | Function | Before | After | Delta | Reason |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for reg in regressions:
        lines.append(_regression_markdown_row(reg, links=links))
    return "\n".join(lines) + "\n"


def render_regressions_pr_comment(
    regressions: list[Regression],
    *,
    links: SourceLinks | None = None,
) -> str:
    lines = [
        PR_COMMENT_MARKER,
        "# riskratchet",
        "",
    ]
    if not regressions:
        lines.append("_No risk regressions detected._")
        return "\n".join(lines) + "\n"
    lines.extend(
        [
            "| Kind | Function | Before | After | Delta | Reason |",
            "| --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    lines.extend(_regression_markdown_row(reg, links=links) for reg in regressions)
    return "\n".join(lines) + "\n"


def render_regressions_summary_json(
    regressions: list[Regression],
    *,
    diff_report: DiffReport | None = None,
) -> str:
    return _summary_envelope("check", _regressions_summary(regressions, diff_report=diff_report))


def render_regressions_summary_text(
    regressions: list[Regression],
    *,
    diff_report: DiffReport | None = None,
) -> str:
    summary = _regressions_summary(regressions, diff_report=diff_report)
    by_kind = summary["by_kind"]
    lines = [
        (
            "check "
            f"regressions={summary['regressions']} "
            f"new_above_threshold={by_kind['new_above_threshold']} "
            f"regressed={by_kind['regressed']} "
            f"existing_above_threshold={by_kind['existing_above_threshold']} "
            f"component_regressed={by_kind['component_regressed']}"
        )
    ]
    if "diff" in summary:
        diff_summary = summary["diff"]
        lines.append(
            "diff "
            f"regressed={diff_summary['regressed']} "
            f"component_regressed={diff_summary['component_regressed']} "
            f"improved={diff_summary['improved']} "
            f"new={diff_summary['new']} "
            f"ambiguous_rename={diff_summary['ambiguous_rename']} "
            f"removed={diff_summary['removed']} "
            f"moved={diff_summary['moved']} "
            f"unchanged={diff_summary['unchanged']}"
        )
    lines.extend(_group_summary_lines(summary.get("groups", {})))
    return "\n".join(lines) + "\n"


def render_regressions_sarif(regressions: list[Regression]) -> str:
    return json.dumps(_sarif_log([_regression_sarif_result(reg) for reg in regressions]), indent=2) + "\n"


def render_regressions_github(regressions: list[Regression]) -> str:
    lines = []
    for reg in regressions:
        if reg.current is not None:
            lines.append(_github_annotation(reg.current, message=reg.reason))
        else:
            lines.append(f"::warning file={reg.id.path}::{_escape_github(reg.reason)}")
    return "\n".join(lines) + ("\n" if lines else "")


def render_diff_table(report: DiffReport) -> str:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, color_system=None, width=120)
    table = Table(title="riskratchet diff", show_header=True, header_style="bold")
    table.add_column("Status")
    table.add_column("Function", no_wrap=True)
    table.add_column("Before", justify="right")
    table.add_column("After", justify="right")
    table.add_column("Delta", justify="right")
    table.add_column("Reason")
    for entry in report.entries:
        table.add_row(
            entry.status.value,
            entry.id.as_target(),
            _fmt_optional(entry.previous_score),
            _fmt_optional(entry.current_score),
            _fmt_optional(entry.delta, signed=True),
            entry.reason,
        )
    console.print(table)
    return buf.getvalue()


def render_diff_json(report: DiffReport) -> str:
    return (
        json.dumps(
            {
                "$schema": DIFF_SCHEMA_URL,
                "version": OUTPUT_VERSION,
                "summary": _diff_summary(report),
                "entries": [_diff_entry_payload(entry) for entry in report.entries],
            },
            indent=2,
        )
        + "\n"
    )


def render_diff_summary_json(report: DiffReport) -> str:
    return _summary_envelope("diff", _diff_summary(report))


def render_diff_summary_text(report: DiffReport) -> str:
    summary = _diff_summary(report)
    lines = [
        (
            "diff "
            f"regressed={summary['regressed']} "
            f"component_regressed={summary['component_regressed']} "
            f"improved={summary['improved']} "
            f"new={summary['new']} "
            f"ambiguous_rename={summary['ambiguous_rename']} "
            f"removed={summary['removed']} "
            f"moved={summary['moved']} "
            f"unchanged={summary['unchanged']}"
        )
    ]
    lines.extend(_group_summary_lines(summary.get("groups", {})))
    return "\n".join(lines) + "\n"


def render_diff_markdown(report: DiffReport, *, links: SourceLinks | None = None) -> str:
    lines = [
        "# riskratchet diff",
        "",
        _diff_summary_line(report),
        "",
        "| Status | Function | Before | After | Delta | Reason |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for entry in report.entries:
        lines.append(_diff_markdown_row(entry, links=links))
    return "\n".join(lines) + "\n"


def render_diff_pr_comment(report: DiffReport, *, links: SourceLinks | None = None) -> str:
    visible = [
        entry
        for entry in report.entries
        if entry.status
        in {
            DiffStatus.REGRESSED,
            DiffStatus.COMPONENT_REGRESSED,
            DiffStatus.AMBIGUOUS_RENAME,
            DiffStatus.NEW,
        }
    ]
    lines = [
        PR_COMMENT_MARKER,
        "# riskratchet",
        "",
        _diff_summary_line(report),
        "",
    ]
    if visible:
        lines.extend(
            [
                "| Status | Function | Before | After | Delta | Reason |",
                "| --- | --- | ---: | ---: | ---: | --- |",
            ]
        )
        lines.extend(_diff_markdown_row(entry, links=links) for entry in visible)
    else:
        lines.append("_No risk regressions detected._")
    for status, title in (
        (DiffStatus.IMPROVED, "Improvements"),
        (DiffStatus.MOVED, "Moved functions"),
        (DiffStatus.REMOVED, "Removed functions"),
        (DiffStatus.UNCHANGED, "Unchanged functions"),
    ):
        entries = [entry for entry in report.entries if entry.status is status]
        if entries:
            lines.extend(["", f"<details><summary>{title} ({len(entries)})</summary>", ""])
            lines.extend(
                [
                    "| Status | Function | Before | After | Delta | Reason |",
                    "| --- | --- | ---: | ---: | ---: | --- |",
                ]
            )
            lines.extend(_diff_markdown_row(entry, links=links) for entry in entries[:20])
            if len(entries) > 20:
                lines.append(f"_... {len(entries) - 20} more hidden._")
            lines.extend(["", "</details>"])
    return "\n".join(lines) + "\n"


def render_diff_github(report: DiffReport) -> str:
    lines = []
    for entry in report.entries:
        if entry.status not in {
            DiffStatus.REGRESSED,
            DiffStatus.COMPONENT_REGRESSED,
            DiffStatus.AMBIGUOUS_RENAME,
            DiffStatus.NEW,
        }:
            continue
        if entry.current is not None:
            lines.append(_github_annotation(entry.current, message=entry.reason))
    return "\n".join(lines) + ("\n" if lines else "")


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


def _markdown_row(
    fn: FunctionRisk,
    *,
    links: SourceLinks | None = None,
    include_group: bool = False,
) -> str:
    target = f"`{fn.id.as_target()}`"
    if links is not None:
        target = f"[{target}]({links.link_for(fn)})"
    cells = [
        severity(fn.score).value,
        f"{fn.score:.1f}",
        f"{fn.crap:.1f}",
        str(fn.complexity.cyclomatic),
        f"{round(fn.coverage.line_coverage * 100)}%",
        _branch_markdown(fn),
    ]
    if include_group:
        cells.append(fn.group or "ungrouped")
    cells.extend([target, f"{fn.span.start_line}-{fn.span.end_line}"])
    return "| " + " | ".join(cells) + " |"


def _regression_markdown_row(reg: Regression, *, links: SourceLinks | None = None) -> str:
    target = f"`{reg.id.as_target()}`"
    if links is not None and reg.current is not None:
        target = f"[{target}]({links.link_for(reg.current)})"
    cells = [
        reg.kind.value,
        target,
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
    analyzed = report.analyzed_functions if report.analyzed_functions is not None else len(report.functions)
    emitted = len(report.functions)
    return {
        "total_functions": emitted,
        "analyzed_functions": analyzed,
        "emitted_functions": emitted,
        "total_files": len(report.files),
        "coverage_status": report.coverage_status,
        "suppressed_functions": report.suppressed_functions,
        "skipped_missing_coverage": report.skipped_missing_coverage,
        "by_severity": counts,
        "groups": _function_group_summary(report.functions),
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
        "group": fn.group,
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


def _regression_payload(reg: Regression) -> dict[str, Any]:
    return {
        "path": reg.id.path,
        "qualname": reg.id.qualname,
        "kind": reg.kind.value,
        "current_score": reg.current_score,
        "previous_score": reg.previous_score,
        "delta": reg.delta,
        "reason": reg.reason,
    }


def _diff_entry_payload(entry: DiffEntry) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "path": entry.id.path,
        "qualname": entry.id.qualname,
        "group": entry.group,
        "status": entry.status.value,
        "current_score": entry.current_score,
        "previous_score": entry.previous_score,
        "delta": entry.delta,
        "previous_path": entry.previous_id.path if entry.previous_id else None,
        "previous_qualname": entry.previous_id.qualname if entry.previous_id else None,
        "reason": entry.reason,
        "previous_targets": [{"path": fid.path, "qualname": fid.qualname} for fid in entry.previous_targets],
        "match_confidence": (
            round(entry.match_confidence, 4) if entry.match_confidence is not None else None
        ),
    }
    return payload


def _diff_summary(report: DiffReport) -> dict[str, Any]:
    summary: dict[str, Any] = {status.value: len(report.by_status(status)) for status in DiffStatus}
    summary["groups"] = _diff_group_summary(report.entries)
    return summary


def _diff_summary_line(report: DiffReport) -> str:
    summary = _diff_summary(report)
    parts = [
        f"**Regressions:** {summary['regressed'] + summary['component_regressed']}",
        f"**New:** {summary['new']}",
        f"**Ambiguous renames:** {summary['ambiguous_rename']}",
        f"**Improved:** {summary['improved']}",
        f"**Moved:** {summary['moved']}",
        f"**Removed:** {summary['removed']}",
    ]
    return " · ".join(parts)


def _diff_markdown_row(entry: DiffEntry, *, links: SourceLinks | None = None) -> str:
    target = f"`{entry.id.as_target()}`"
    if links is not None and entry.current is not None:
        target = f"[{target}]({links.link_for(entry.current)})"
    cells = [
        entry.status.value,
        target,
        _fmt_optional(entry.previous_score),
        _fmt_optional(entry.current_score),
        _fmt_optional(entry.delta, signed=True),
        entry.reason,
    ]
    return "| " + " | ".join(cells) + " |"


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
    extra = []
    if report.suppressed_functions:
        extra.append(f"{report.suppressed_functions} suppressed")
    if report.skipped_missing_coverage:
        extra.append(f"{report.skipped_missing_coverage} skipped missing coverage")
    suffix = ("; " + ", ".join(extra)) if extra else ""
    summary = f"Summary: {len(report.functions)} functions across {len(report.files)} files. "
    return summary + ", ".join(parts) + suffix


def _github_annotation(fn: FunctionRisk, *, message: str | None = None) -> str:
    text = message or (
        f"{fn.id.as_target()} has {severity(fn.score).value} risk: "
        f"score {fn.score:.1f}, CRAP {fn.crap:.1f}, complexity {fn.complexity.cyclomatic}"
    )
    return (
        f"::warning file={fn.id.path},line={fn.span.start_line},endLine={fn.span.end_line}"
        f"::{_escape_github(text)}"
    )


def _escape_github(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A").replace(":", "%3A")


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


def _summary_envelope(command: str, summary: dict[str, Any]) -> str:
    return (
        json.dumps(
            {
                "$schema": SUMMARY_SCHEMA_URL,
                "version": OUTPUT_VERSION,
                "command": command,
                "summary": summary,
            },
            indent=2,
        )
        + "\n"
    )


def _function_group_summary(functions: Iterable[FunctionRisk]) -> dict[str, dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for fn in functions:
        name = fn.group or "ungrouped"
        bucket = groups.setdefault(
            name,
            {
                "functions": 0,
                "max_score": None,
                "by_severity": {sev.value: 0 for sev in Severity},
            },
        )
        bucket["functions"] += 1
        bucket["max_score"] = fn.score if bucket["max_score"] is None else max(bucket["max_score"], fn.score)
        bucket["by_severity"][severity(fn.score).value] += 1
    return groups


def _diff_group_summary(entries: Iterable[DiffEntry]) -> dict[str, dict[str, int]]:
    groups: dict[str, dict[str, int]] = {}
    for entry in entries:
        name = entry.group or "ungrouped"
        bucket = groups.setdefault(name, {status.value: 0 for status in DiffStatus})
        bucket[entry.status.value] += 1
    return groups


def _regressions_summary(
    regressions: list[Regression],
    *,
    diff_report: DiffReport | None = None,
) -> dict[str, Any]:
    by_kind = {kind.value: 0 for kind in RegressionKind}
    groups: dict[str, dict[str, int]] = {}
    for reg in regressions:
        by_kind[reg.kind.value] += 1
        name = (reg.current.group if reg.current is not None else None) or "ungrouped"
        bucket = groups.setdefault(name, {kind.value: 0 for kind in RegressionKind})
        bucket[reg.kind.value] += 1
    summary: dict[str, Any] = {
        "regressions": len(regressions),
        "by_kind": by_kind,
        "groups": groups,
    }
    if diff_report is not None:
        summary["diff"] = _diff_summary(diff_report)
        if not groups:
            summary["groups"] = summary["diff"].get("groups", {})
    return summary


def _severity_summary_line(counts: dict[str, int]) -> str:
    return (
        "severity "
        f"low={counts['low']} "
        f"medium={counts['medium']} "
        f"high={counts['high']} "
        f"critical={counts['critical']}"
    )


def _group_summary_lines(groups: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for name in sorted(groups):
        values = groups[name]
        parts = [f"group name={name}"]
        for key in sorted(values):
            value = values[key]
            if isinstance(value, dict):
                for nested_key in sorted(value):
                    parts.append(f"{key}.{nested_key}={value[nested_key]}")
            else:
                parts.append(f"{key}={value}")
        lines.append(" ".join(parts))
    return lines
