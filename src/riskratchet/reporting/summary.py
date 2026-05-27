"""Shared helpers and constants for the reporting package.

This module is the dependency leaf: it must not import any other
reporting submodule. Other submodules (`text`, `markdown`, `sarif`,
`json_payload`, `annotations`) import from here for cell-level
formatters, summary-payload builders, and the schema URL constants.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

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


def _sorted_by_risk(functions: Iterable[FunctionRisk]) -> list[FunctionRisk]:
    return sorted(functions, key=lambda fn: (-fn.score, fn.id.as_target()))


def _fmt_optional(value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return "n/a"
    if signed:
        return f"{value:+.1f}"
    return f"{value:.1f}"


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


def _diff_summary(report: DiffReport) -> dict[str, Any]:
    summary: dict[str, Any] = {status.value: len(report.by_status(status)) for status in DiffStatus}
    summary["groups"] = _diff_group_summary(report.entries)
    return summary


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
