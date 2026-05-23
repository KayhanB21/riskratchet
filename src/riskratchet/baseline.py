"""Baseline JSON I/O and regression detection.

The baseline is the canonical "what we tolerated last time" snapshot. Compare
a fresh `RiskReport` against it and surface only the functions that crossed
the configured thresholds: new functions above `fail_new_above`, existing
functions whose score grew by more than `fail_regression_above`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from riskratchet.models import (
    Baseline,
    BaselineEntry,
    FunctionId,
    Regression,
    RegressionKind,
    RiskComponents,
    RiskReport,
)

BASELINE_VERSION = "1"


def baseline_from_report(report: RiskReport) -> Baseline:
    entries: dict[FunctionId, BaselineEntry] = {}
    for fn in report.functions:
        entries[fn.id] = BaselineEntry(
            id=fn.id,
            score=round(fn.score, 4),
            components=fn.components,
        )
    return Baseline(version=BASELINE_VERSION, entries=entries)


def save_baseline(baseline: Baseline, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dumps(baseline), encoding="utf-8")


def load_baseline(path: Path) -> Baseline:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read baseline {path}: {exc}") from exc

    version = str(raw.get("version", BASELINE_VERSION))
    entries: dict[FunctionId, BaselineEntry] = {}
    for raw_entry in raw.get("entries", []):
        entry = _entry_from_dict(raw_entry)
        if entry is not None:
            entries[entry.id] = entry
    return Baseline(version=version, entries=entries)


def compare(
    new: RiskReport,
    old: Baseline,
    *,
    fail_new_above: float,
    fail_regression_above: float,
) -> list[Regression]:
    """Return regressions found between `old` (baseline) and `new` (report).

    Existing functions are flagged only when `new.score - old.score >
    fail_regression_above`; the strict comparison preserves the "tolerance is
    the noise floor" semantics from the plan. New functions are flagged only
    when their score is above `fail_new_above`.
    """
    out: list[Regression] = []
    for fn in new.functions:
        previous = old.entries.get(fn.id)
        if previous is None:
            if fn.score > fail_new_above:
                out.append(
                    Regression(
                        id=fn.id,
                        kind=RegressionKind.NEW_ABOVE_THRESHOLD,
                        current_score=fn.score,
                        previous_score=None,
                        delta=None,
                        reason=(
                            f"new function with score {fn.score:.1f} "
                            f"exceeds new-function threshold {fail_new_above:.1f}"
                        ),
                        current=fn,
                    )
                )
            continue
        delta = fn.score - previous.score
        if delta > fail_regression_above:
            out.append(
                Regression(
                    id=fn.id,
                    kind=RegressionKind.REGRESSED,
                    current_score=fn.score,
                    previous_score=previous.score,
                    delta=delta,
                    reason=(
                        f"risk grew by {delta:+.1f} "
                        f"(from {previous.score:.1f} to {fn.score:.1f}); "
                        f"tolerance is {fail_regression_above:+.1f}"
                    ),
                    current=fn,
                )
            )
    out.sort(key=lambda r: (-(r.delta or r.current_score), r.id.as_target()))
    return out


def _dumps(baseline: Baseline) -> str:
    payload: dict[str, Any] = {
        "version": baseline.version,
        "entries": [
            _entry_to_dict(entry)
            for entry in sorted(
                baseline.entries.values(),
                key=lambda e: (e.id.path, e.id.qualname),
            )
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def _entry_to_dict(entry: BaselineEntry) -> dict[str, Any]:
    c = entry.components
    return {
        "path": entry.id.path,
        "qualname": entry.id.qualname,
        "score": round(entry.score, 4),
        "components": {
            "coverage_gap": round(c.coverage_gap, 4),
            "structural_complexity": round(c.structural_complexity, 4),
            "branch_gap": round(c.branch_gap, 4),
            "churn": round(c.churn, 4),
            "public_surface": round(c.public_surface, 4),
            "sprawl": round(c.sprawl, 4),
        },
    }


def _entry_from_dict(raw: Any) -> BaselineEntry | None:
    if not isinstance(raw, dict):
        return None
    path = raw.get("path")
    qualname = raw.get("qualname")
    score = raw.get("score")
    components_raw = raw.get("components")
    if not (
        isinstance(path, str)
        and isinstance(qualname, str)
        and isinstance(score, (int, float))
        and isinstance(components_raw, dict)
    ):
        return None
    components = RiskComponents(
        coverage_gap=float(components_raw.get("coverage_gap", 0.0)),
        structural_complexity=float(components_raw.get("structural_complexity", 0.0)),
        branch_gap=float(components_raw.get("branch_gap", 0.0)),
        churn=float(components_raw.get("churn", 0.0)),
        public_surface=float(components_raw.get("public_surface", 0.0)),
        sprawl=float(components_raw.get("sprawl", 0.0)),
    )
    return BaselineEntry(
        id=FunctionId(path=path, qualname=qualname),
        score=float(score),
        components=components,
    )
