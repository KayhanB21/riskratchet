"""SARIF 2.1.0 log construction for `--format sarif`."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any

from riskratchet.models import (
    FunctionRisk,
    Regression,
    RiskReport,
    Severity,
)
from riskratchet.reporting.summary import SourceLinks, _branch_pct, _sorted_by_risk
from riskratchet.scoring import severity

if TYPE_CHECKING:  # TsFunction is a pure dataclass; kept out of the runtime import graph
    from collections.abc import Sequence

    from riskratchet.typescript import TsFunction

_TS_RULE = {
    "id": "riskratchet.typescript-function",
    "name": "TypeScript function (experimental)",
    "shortDescription": {
        "text": "Informational: a discovered TypeScript function. Unscored (no gating) until 0.3.0."
    },
    "helpUri": "https://github.com/KayhanB21/riskratchet",
}


def render_report_sarif(
    report: RiskReport,
    *,
    min_score: float = 25.0,
    links: SourceLinks | None = None,
    ts_functions: Sequence[TsFunction] = (),
) -> str:
    results = [
        _function_sarif_result(fn, links=links)
        for fn in _sorted_by_risk(report.functions)
        if fn.score >= min_score
    ]
    # EXPERIMENTAL (P20 slice 5): unscored TypeScript functions become informational `note`
    # results, present only under `scan --experimental-typescript`. Appended after the scored
    # Python results, so default SARIF output is unchanged.
    ts_results = [_ts_function_sarif_result(fn) for fn in _sorted_ts(ts_functions)]
    return json.dumps(_sarif_log(results + ts_results, include_ts_rule=bool(ts_results)), indent=2) + "\n"


def render_regressions_sarif(regressions: list[Regression], *, links: SourceLinks | None = None) -> str:
    return (
        json.dumps(
            _sarif_log([_regression_sarif_result(reg, links=links) for reg in regressions]),
            indent=2,
        )
        + "\n"
    )


def _sarif_log(results: list[dict[str, Any]], *, include_ts_rule: bool = False) -> dict[str, Any]:
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "riskratchet",
                        "informationUri": "https://github.com/KayhanB21/riskratchet",
                        "rules": _sarif_rules(include_ts_rule=include_ts_rule),
                    }
                },
                "results": results,
            }
        ],
    }


def _sarif_rules(*, include_ts_rule: bool) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = [
        {
            "id": "riskratchet.function-risk",
            "name": "Function maintainability risk",
            "shortDescription": {"text": "Function-level maintainability risk score."},
            "helpUri": "https://github.com/KayhanB21/riskratchet",
        },
        {
            "id": "riskratchet.regression",
            "name": "Risk regression",
            "shortDescription": {"text": "Function risk increased beyond the configured ratchet."},
            "helpUri": "https://github.com/KayhanB21/riskratchet",
        },
    ]
    # The TS rule is added only when TS results are present, so default SARIF output is byte-stable.
    if include_ts_rule:
        rules.append(_TS_RULE)
    return rules


def _function_sarif_result(fn: FunctionRisk, *, links: SourceLinks | None = None) -> dict[str, Any]:
    sev = severity(fn.score)
    properties = _sarif_function_properties(fn)
    if links is not None:
        properties["source_url"] = links.link_for(fn)
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
        "properties": properties,
    }


def _regression_sarif_result(reg: Regression, *, links: SourceLinks | None = None) -> dict[str, Any]:
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
        if links is not None:
            result["properties"]["source_url"] = links.link_for(fn)
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
        # `group`/`language` mirror the JSON payload; both were missing here until 0.2.15 (the
        # 0.2.11 `language` addition and group support never reached the SARIF properties).
        "group": fn.group,
        "language": fn.language,
        "components": {
            "coverage_gap": fn.components.coverage_gap,
            "structural_complexity": fn.components.structural_complexity,
            "branch_gap": fn.components.branch_gap,
            "churn": fn.components.churn,
            "public_surface": fn.components.public_surface,
            "sprawl": fn.components.sprawl,
        },
    }


def _ts_function_sarif_result(fn: TsFunction) -> dict[str, Any]:
    """EXPERIMENTAL: an unscored TypeScript function as an informational `note` result (P20 slice 5).

    Carries no score/severity — TypeScript is informational until 0.3.0 — so it is always `note`
    level and tagged `language: "typescript"` in properties, alongside its identity fingerprints.
    """
    coverage = fn.coverage
    complexity = fn.complexity.cyclomatic if fn.complexity is not None else None
    visibility = "public" if fn.is_public else "internal"
    detail = f", complexity {complexity}" if complexity is not None else ""
    if coverage is not None:
        detail += f", line coverage {coverage.line_coverage * 100:.0f}%"
    return {
        "ruleId": "riskratchet.typescript-function",
        "level": "note",
        "message": {"text": f"{fn.id.as_target()} ({visibility} TypeScript {fn.kind}){detail}."},
        "locations": [_sarif_location(fn.id.path, fn.span.start_line, fn.span.end_line)],
        "properties": {
            "path": fn.id.path,
            "qualname": fn.id.qualname,
            "language": "typescript",
            "kind": fn.kind,
            "is_public": fn.is_public,
            "complexity": complexity,
            "line_coverage": coverage.line_coverage if coverage is not None else None,
            "branch_coverage": coverage.branch_coverage if coverage is not None else None,
            "fingerprint": fn.fingerprint,
            "signature": fn.signature,
        },
    }


def _sorted_ts(functions: Sequence[TsFunction]) -> list[TsFunction]:
    """Stable order (path, start line, qualname) — TS functions carry no risk to sort on."""
    return sorted(functions, key=lambda fn: (fn.id.path, fn.span.start_line, fn.id.qualname))


def _sarif_level_for_severity(sev: Severity) -> str:
    if sev is Severity.CRITICAL:
        return "error"
    if sev in {Severity.MEDIUM, Severity.HIGH}:
        return "warning"
    return "note"
