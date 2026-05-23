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
    FunctionRisk,
    Regression,
    RegressionKind,
    RiskComponents,
    RiskReport,
)

BASELINE_VERSION = "2"


def baseline_from_report(report: RiskReport) -> Baseline:
    entries: dict[FunctionId, BaselineEntry] = {}
    for fn in report.functions:
        entries[fn.id] = BaselineEntry(
            id=fn.id,
            score=round(fn.score, 4),
            components=fn.components,
            fingerprint=fn.fingerprint,
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
    fail_existing_above: float | None = None,
    fail_component_regression_above: float = 15.0,
    component_regression_gate: bool = True,
) -> list[Regression]:
    """Return regressions found between `old` (baseline) and `new` (report).

    Existing functions are flagged only when `new.score - old.score >
    fail_regression_above`; the strict comparison preserves the "tolerance is
    the noise floor" semantics from the plan. New functions are flagged only
    when their score is above `fail_new_above`.
    """
    out: list[Regression] = []
    old_by_fingerprint = _unique_old_entries_by_fingerprint(old)
    current_fingerprint_counts = _current_fingerprint_counts(new)
    used_old_ids: set[FunctionId] = {fn.id for fn in new.functions if fn.id in old.entries}

    for fn in new.functions:
        previous = old.entries.get(fn.id)
        previous_target: str | None = None
        if previous is None:
            previous = _match_by_fingerprint(
                fn,
                old_by_fingerprint,
                current_fingerprint_counts,
                used_old_ids,
            )
            if previous is not None:
                previous_target = previous.id.as_target()

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

        used_old_ids.add(previous.id)
        delta = fn.score - previous.score
        if delta > fail_regression_above:
            previous_note = f" after matching previous target {previous_target}" if previous_target else ""
            out.append(
                Regression(
                    id=fn.id,
                    kind=RegressionKind.REGRESSED,
                    current_score=fn.score,
                    previous_score=previous.score,
                    delta=delta,
                    reason=(
                        f"risk grew by {delta:+.1f}{previous_note} "
                        f"(from {previous.score:.1f} to {fn.score:.1f}); "
                        f"tolerance is {fail_regression_above:+.1f}"
                    ),
                    current=fn,
                )
            )
            continue

        if component_regression_gate:
            component_regression = _component_regression(
                fn.components,
                previous.components,
                tolerance=fail_component_regression_above,
            )
            if component_regression is not None:
                name, previous_value, current_value, component_delta = component_regression
                out.append(
                    Regression(
                        id=fn.id,
                        kind=RegressionKind.COMPONENT_REGRESSED,
                        current_score=fn.score,
                        previous_score=previous.score,
                        delta=component_delta,
                        reason=(
                            f"{name} grew by {component_delta:+.1f} "
                            f"(from {previous_value:.1f} to {current_value:.1f}); "
                            f"component tolerance is {fail_component_regression_above:+.1f}"
                        ),
                        current=fn,
                    )
                )
                continue

        if fail_existing_above is not None and fn.score > fail_existing_above:
            out.append(
                Regression(
                    id=fn.id,
                    kind=RegressionKind.EXISTING_ABOVE_THRESHOLD,
                    current_score=fn.score,
                    previous_score=previous.score,
                    delta=delta,
                    reason=(
                        f"existing function score {fn.score:.1f} "
                        f"exceeds existing-risk threshold {fail_existing_above:.1f}"
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
    payload: dict[str, Any] = {
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
    if entry.fingerprint is not None:
        payload["fingerprint"] = entry.fingerprint
    return payload


def _entry_from_dict(raw: Any) -> BaselineEntry | None:
    if not isinstance(raw, dict):
        return None
    path = raw.get("path")
    qualname = raw.get("qualname")
    score = raw.get("score")
    components_raw = raw.get("components")
    fingerprint = raw.get("fingerprint")
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
        fingerprint=fingerprint if isinstance(fingerprint, str) else None,
    )


def _unique_old_entries_by_fingerprint(old: Baseline) -> dict[str, BaselineEntry | None]:
    by_fingerprint: dict[str, BaselineEntry | None] = {}
    for entry in old.entries.values():
        if entry.fingerprint is None:
            continue
        if entry.fingerprint in by_fingerprint:
            by_fingerprint[entry.fingerprint] = None
        else:
            by_fingerprint[entry.fingerprint] = entry
    return by_fingerprint


def _current_fingerprint_counts(report: RiskReport) -> dict[str, int]:
    counts: dict[str, int] = {}
    for fn in report.functions:
        if fn.fingerprint is not None:
            counts[fn.fingerprint] = counts.get(fn.fingerprint, 0) + 1
    return counts


def _match_by_fingerprint(
    fn: FunctionRisk,
    old_by_fingerprint: dict[str, BaselineEntry | None],
    current_fingerprint_counts: dict[str, int],
    used_old_ids: set[FunctionId],
) -> BaselineEntry | None:
    if fn.fingerprint is None or current_fingerprint_counts.get(fn.fingerprint) != 1:
        return None
    entry = old_by_fingerprint.get(fn.fingerprint)
    if entry is None or entry.id in used_old_ids:
        return None
    return entry


def _component_regression(
    current: RiskComponents,
    previous: RiskComponents,
    *,
    tolerance: float,
) -> tuple[str, float, float, float] | None:
    regressions: list[tuple[str, float, float, float]] = []
    for name in (
        "coverage_gap",
        "structural_complexity",
        "branch_gap",
        "churn",
        "public_surface",
        "sprawl",
    ):
        previous_value = float(getattr(previous, name))
        current_value = float(getattr(current, name))
        delta = current_value - previous_value
        if delta > tolerance:
            regressions.append((name, previous_value, current_value, delta))
    if not regressions:
        return None
    return max(regressions, key=lambda item: item[3])
