"""Native JSON renderers — `riskratchet --format json`.

All JSON outputs share `$schema` and `version` envelopes through
constants defined in `summary.py`. This module owns the per-function /
per-regression / per-diff payload shape; the schemas referenced live
externally (URLs only).
"""

from __future__ import annotations

import json
from typing import Any

from riskratchet.models import (
    DiffEntry,
    DiffReport,
    FunctionRisk,
    Regression,
    RiskReport,
)
from riskratchet.reporting.summary import (
    DIFF_SCHEMA_URL,
    OUTPUT_VERSION,
    REGRESSIONS_SCHEMA_URL,
    REPORT_SCHEMA_URL,
    SUMMARY_SCHEMA_URL,
    _diff_summary,
    _regressions_summary,
    _sorted_by_risk,
    _summary_payload,
)
from riskratchet.scoring import severity


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


def render_regressions_summary_json(
    regressions: list[Regression],
    *,
    diff_report: DiffReport | None = None,
) -> str:
    return _summary_envelope("check", _regressions_summary(regressions, diff_report=diff_report))


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
