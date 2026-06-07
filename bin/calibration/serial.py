"""Serialize/deserialize ``FunctionRisk`` records for the per-SHA analyze cache.

The cached ``analyze.json`` is what makes candidate re-scoring (``rescore.py``)
reproducible offline: given the components + span + file_stats of every function
at base and head, the candidate sprawl recompute and re-diff need no re-clone and
no coverage regeneration. Round-trips a full ``FunctionRisk`` so a reconstructed
``RiskReport`` can be fed straight back into ``baseline_from_report`` / ``diff``.
"""

from __future__ import annotations

from riskratchet.models import (
    ChurnStats,
    ComplexityStats,
    CoverageStats,
    FileStats,
    FunctionId,
    FunctionRisk,
    FunctionSpan,
    RiskComponents,
    RiskReport,
)

SCHEMA = 1


def function_to_dict(fn: FunctionRisk) -> dict[str, object]:
    return {
        "id": {"path": fn.id.path, "qualname": fn.id.qualname},
        "span": {"start_line": fn.span.start_line, "end_line": fn.span.end_line},
        "is_public": fn.is_public,
        "complexity": {"cyclomatic": fn.complexity.cyclomatic},
        "coverage": {
            "line_coverage": fn.coverage.line_coverage,
            "branch_coverage": fn.coverage.branch_coverage,
            "missing_lines": list(fn.coverage.missing_lines),
            "missing_branches": [list(b) for b in fn.coverage.missing_branches],
        },
        "churn": {"commits": fn.churn.commits},
        "file_stats": {
            "path": fn.file_stats.path,
            "total_lines": fn.file_stats.total_lines,
            "function_count": fn.file_stats.function_count,
        },
        "components": {
            "coverage_gap": fn.components.coverage_gap,
            "structural_complexity": fn.components.structural_complexity,
            "branch_gap": fn.components.branch_gap,
            "churn": fn.components.churn,
            "public_surface": fn.components.public_surface,
            "sprawl": fn.components.sprawl,
        },
        "score": fn.score,
        "crap": fn.crap,
        "fingerprint": fn.fingerprint,
        "signature": fn.signature,
        "group": fn.group,
    }


def report_to_dict(report: RiskReport) -> dict[str, object]:
    return {
        "schema": SCHEMA,
        "coverage_status": report.coverage_status,
        "functions": [function_to_dict(fn) for fn in report.functions],
    }


def _d(obj: object, key: str) -> dict[str, object]:
    value = obj
    assert isinstance(value, dict)
    inner = value[key]
    assert isinstance(inner, dict)
    return inner


def _as_int(value: object) -> int:
    assert isinstance(value, int)
    return value


def _as_float(value: object) -> float:
    assert isinstance(value, (int, float))
    return float(value)


def _as_str(value: object) -> str:
    assert isinstance(value, str)
    return value


def _as_list(value: object) -> list[object]:
    assert isinstance(value, list)
    return value


def function_from_dict(raw: dict[str, object]) -> FunctionRisk:
    fid = _d(raw, "id")
    span = _d(raw, "span")
    complexity = _d(raw, "complexity")
    coverage = _d(raw, "coverage")
    churn = _d(raw, "churn")
    file_stats = _d(raw, "file_stats")
    components = _d(raw, "components")
    branch_cov = coverage["branch_coverage"]
    return FunctionRisk(
        id=FunctionId(path=_as_str(fid["path"]), qualname=_as_str(fid["qualname"])),
        span=FunctionSpan(start_line=_as_int(span["start_line"]), end_line=_as_int(span["end_line"])),
        is_public=bool(raw["is_public"]),
        complexity=ComplexityStats(cyclomatic=_as_int(complexity["cyclomatic"])),
        coverage=CoverageStats(
            line_coverage=_as_float(coverage["line_coverage"]),
            branch_coverage=None if branch_cov is None else _as_float(branch_cov),
            missing_lines=tuple(_as_int(x) for x in _as_list(coverage["missing_lines"])),
            missing_branches=tuple(
                (_as_int(_as_list(b)[0]), _as_int(_as_list(b)[1]))
                for b in _as_list(coverage["missing_branches"])
            ),
        ),
        churn=ChurnStats(commits=_as_int(churn["commits"])),
        file_stats=FileStats(
            path=_as_str(file_stats["path"]),
            total_lines=_as_int(file_stats["total_lines"]),
            function_count=_as_int(file_stats["function_count"]),
        ),
        components=RiskComponents(
            coverage_gap=_as_float(components["coverage_gap"]),
            structural_complexity=_as_float(components["structural_complexity"]),
            branch_gap=_as_float(components["branch_gap"]),
            churn=_as_float(components["churn"]),
            public_surface=_as_float(components["public_surface"]),
            sprawl=_as_float(components["sprawl"]),
        ),
        score=_as_float(raw["score"]),
        crap=_as_float(raw["crap"]),
        fingerprint=None if raw["fingerprint"] is None else _as_str(raw["fingerprint"]),
        signature=None if raw["signature"] is None else _as_str(raw["signature"]),
        group=None if raw["group"] is None else _as_str(raw["group"]),
    )


def report_from_dict(raw: dict[str, object]) -> RiskReport:
    functions_raw = raw["functions"]
    assert isinstance(functions_raw, list)
    functions = tuple(function_from_dict(f) for f in functions_raw)
    files = tuple({fn.file_stats for fn in functions})
    status = raw.get("coverage_status", "missing")
    return RiskReport(functions=functions, files=files, coverage_status=str(status))
