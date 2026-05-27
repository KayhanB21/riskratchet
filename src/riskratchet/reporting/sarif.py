"""SARIF 2.1.0 log construction for `--format sarif`."""

from __future__ import annotations

import json
import os
from typing import Any

from riskratchet.models import (
    FunctionRisk,
    Regression,
    RiskReport,
    Severity,
)
from riskratchet.reporting.summary import _branch_pct, _sorted_by_risk
from riskratchet.scoring import severity


def render_report_sarif(report: RiskReport, *, min_score: float = 25.0) -> str:
    results = [
        _function_sarif_result(fn) for fn in _sorted_by_risk(report.functions) if fn.score >= min_score
    ]
    return json.dumps(_sarif_log(results), indent=2) + "\n"


def render_regressions_sarif(regressions: list[Regression]) -> str:
    return json.dumps(_sarif_log([_regression_sarif_result(reg) for reg in regressions]), indent=2) + "\n"


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
