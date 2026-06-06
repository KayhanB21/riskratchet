"""Candidate sprawl re-scoring + accepted/rejected separation analysis.

The P24 finding handed P21 three candidate fixes for the sprawl component. None is
a top-level weight override: the file-line term lives *inside* the blended sprawl
component (``sprawl_score`` = mean of a function-length term and a file-length
term), so each candidate **recomputes the component** per function and re-runs
``total_risk`` — never ``analyze(weights=...)``. We then re-diff each labelled PR
under each candidate and ask: does the candidate make *rejected* PRs carry more
regressions than *accepted* ones? Analysis only; ships no product weight change.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from bin.calibration import stats
from bin.calibration.replay import replay_reports
from riskratchet.models import FunctionRisk, RiskReport
from riskratchet.scoring import (
    DEFAULT_WEIGHTS,
    FILE_LINE_FREE,
    FILE_LINE_SATURATION,
    FUNCTION_LINE_FREE,
    FUNCTION_LINE_SATURATION,
    _saturate,
    total_risk,
)

# Candidate (c): "raise the 500/1000 band" so only true god-modules move.
RAISED_FILE_LINE_FREE = 1000.0
RAISED_FILE_LINE_SATURATION = 2000.0

SprawlFn = Callable[[FunctionRisk], float]


def _function_term(fn: FunctionRisk) -> float:
    return _saturate(fn.span.line_count, free=FUNCTION_LINE_FREE, saturation=FUNCTION_LINE_SATURATION)


def _file_term(
    fn: FunctionRisk, *, free: float = FILE_LINE_FREE, saturation: float = FILE_LINE_SATURATION
) -> float:
    return _saturate(fn.file_stats.total_lines, free=free, saturation=saturation)


def _baseline_sprawl(fn: FunctionRisk) -> float:
    return (_function_term(fn) + _file_term(fn)) / 2.0


def _drop_file_line(fn: FunctionRisk) -> float:
    # (a) sprawl = function-length term only.
    return _function_term(fn)


def _shrink_file_share(fn: FunctionRisk) -> float:
    # (b) reweight the two halves 0.75 function / 0.25 file (was 0.5 / 0.5).
    return 0.75 * _function_term(fn) + 0.25 * _file_term(fn)


def _raise_band(fn: FunctionRisk) -> float:
    # (c) raise the file-line band so only true god-modules contribute.
    file_term = _file_term(fn, free=RAISED_FILE_LINE_FREE, saturation=RAISED_FILE_LINE_SATURATION)
    return (_function_term(fn) + file_term) / 2.0


@dataclass(frozen=True)
class Candidate:
    key: str
    description: str
    sprawl: SprawlFn


CANDIDATES: tuple[Candidate, ...] = (
    Candidate("baseline", "current shipped scoring (control)", _baseline_sprawl),
    Candidate("drop_file_line", "sprawl = function-length term only", _drop_file_line),
    Candidate("shrink_file_share", "0.75 function / 0.25 file blend", _shrink_file_share),
    Candidate("raise_band", "file-line band raised 500/1000 -> 1000/2000", _raise_band),
)


def rescore_report(report: RiskReport, candidate: Candidate) -> RiskReport:
    """Return a copy of ``report`` with sprawl + total score recomputed.

    Only the sprawl component and the resulting total change; all other
    components, the fingerprint, and the signature are preserved so rename
    matching in ``diff`` still works.
    """
    rescored: list[FunctionRisk] = []
    for fn in report.functions:
        components = replace(fn.components, sprawl=candidate.sprawl(fn))
        score = total_risk(components, weights=DEFAULT_WEIGHTS)
        rescored.append(replace(fn, components=components, score=score))
    return replace(report, functions=tuple(rescored))


def regression_count_under(
    candidate: Candidate,
    base_report: RiskReport,
    head_report: RiskReport,
    *,
    fail_regression_above: float = 5.0,
) -> int:
    """Regressions of head vs base when both are scored under ``candidate``."""
    record = replay_reports(
        repo="-",
        pr=0,
        base_sha="-",
        head_sha="-",
        base_report=rescore_report(base_report, candidate),
        head_report=rescore_report(head_report, candidate),
        fail_regression_above=fail_regression_above,
    )
    return record.regression_count


@dataclass(frozen=True)
class LabeledPr:
    repo: str
    pr: int
    label: str  # "accepted" | "rejected"
    base_report: RiskReport
    head_report: RiskReport


@dataclass(frozen=True)
class CandidateSeparation:
    key: str
    description: str
    accepted_counts: tuple[int, ...]
    rejected_counts: tuple[int, ...]

    def to_dict(self) -> dict[str, object]:
        sep = stats.mann_whitney_u(
            [float(c) for c in self.rejected_counts],
            [float(c) for c in self.accepted_counts],
        )
        return {
            "candidate": self.key,
            "description": self.description,
            "n_accepted": len(self.accepted_counts),
            "n_rejected": len(self.rejected_counts),
            "accepted_mean": _mean(self.accepted_counts),
            "rejected_mean": _mean(self.rejected_counts),
            "accepted_median": _median(self.accepted_counts),
            "rejected_median": _median(self.rejected_counts),
            # effect > 0 / z > 0 => rejected PRs carry MORE regressions (desired).
            "separation_effect": _round_or_none(sep["effect"]),
            "separation_z": _round_or_none(sep["z"]),
        }


def evaluate(prs: list[LabeledPr], *, fail_regression_above: float = 5.0) -> list[dict[str, object]]:
    """Score every candidate's accept/reject separation over the labelled PRs."""
    results: list[dict[str, object]] = []
    for candidate in CANDIDATES:
        accepted: list[int] = []
        rejected: list[int] = []
        for pr in prs:
            count = regression_count_under(
                candidate, pr.base_report, pr.head_report, fail_regression_above=fail_regression_above
            )
            (rejected if pr.label == "rejected" else accepted).append(count)
        results.append(
            CandidateSeparation(
                key=candidate.key,
                description=candidate.description,
                accepted_counts=tuple(accepted),
                rejected_counts=tuple(rejected),
            ).to_dict()
        )
    return results


def _mean(values: tuple[int, ...]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _median(values: tuple[int, ...]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return float(s[mid]) if len(s) % 2 else round((s[mid - 1] + s[mid]) / 2.0, 3)


def _round_or_none(value: float) -> float | None:
    return None if value != value else round(value, 4)  # NaN check: NaN != NaN
