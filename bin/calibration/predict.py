"""Predictive validity: does the score (and the sprawl component) predict defects?

The population is every function at the snapshot S; the label is "defect-implicated"
(from SZZ). For each sprawl candidate we re-score the snapshot and compute the AUC
of the total score against the label, plus the AUC of the sprawl component alone.

AUC here = P(score of a buggy function > score of a clean one) = U / (n_buggy *
n_clean), which is exactly the rank-biserial `effect` from `stats.mann_whitney_u`
mapped from [-1, 1] to [0, 1] — so no new statistics code.

Readout: if `drop_file_line` raises total AUC above `baseline`, the file-line term
is noise; if it lowers it, the term is signal. `baseline` sprawl_auc ~ 0.5 means the
component is non-predictive regardless of blend.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from bin.calibration import stats
from bin.calibration.defects import DefectLabels, SnapshotPopulation
from bin.calibration.rescore import CANDIDATES, rescore_report
from riskratchet.models import FunctionId, FunctionRisk, RiskReport


def auc_from_mwu(buggy: list[float], clean: list[float]) -> float:
    """AUC = (rank-biserial effect + 1) / 2. NaN when either group is empty."""
    effect = stats.mann_whitney_u(buggy, clean)["effect"]
    return float("nan") if effect != effect else (effect + 1.0) / 2.0  # effect != effect: NaN


def _split(
    report: RiskReport, defect_ids: set[FunctionId], pick: Callable[[FunctionRisk], float]
) -> tuple[list[float], list[float]]:
    buggy: list[float] = []
    clean: list[float] = []
    for fn in report.functions:
        (buggy if fn.id in defect_ids else clean).append(pick(fn))
    return buggy, clean


@dataclass(frozen=True)
class CandidateAuc:
    candidate: str
    total_auc: float
    sprawl_auc: float
    z: float

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate": self.candidate,
            "total_auc": _round_or_none(self.total_auc),
            "sprawl_auc": _round_or_none(self.sprawl_auc),
            "z": _round_or_none(self.z),
        }


def evaluate_candidates(snapshot: SnapshotPopulation, labels: DefectLabels) -> list[CandidateAuc]:
    defect_ids = set(labels.counts)
    results: list[CandidateAuc] = []
    for candidate in CANDIDATES:
        rescored = rescore_report(snapshot.report, candidate)
        total_buggy, total_clean = _split(rescored, defect_ids, lambda fn: fn.score)
        sprawl_buggy, sprawl_clean = _split(rescored, defect_ids, lambda fn: fn.components.sprawl)
        z = stats.mann_whitney_u(total_buggy, total_clean)["z"]
        results.append(
            CandidateAuc(
                candidate=candidate.key,
                total_auc=auc_from_mwu(total_buggy, total_clean),
                sprawl_auc=auc_from_mwu(sprawl_buggy, sprawl_clean),
                z=z,
            )
        )
    return results


def _round_or_none(value: float) -> float | None:
    return None if value != value else round(value, 4)  # value != value: NaN
