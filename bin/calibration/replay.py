"""In-process PR-replay core: turn a base/head pair into an outcome record.

Reuses riskratchet's own engine internals rather than shelling out to the CLI:
``baseline_from_report`` turns the base-commit report into a baseline, and
``diff`` classifies the head report against it (regressed, component-regressed,
moved, ambiguous-rename, new ...), carrying the rename ``match_confidence`` we
record. Checkout + coverage regeneration live in ``coverage_replay.py``; this
module is pure given two analyzable source trees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from bin.calibration.config import PrLabel
from bin.calibration.corpus import analyze_report
from riskratchet.baseline import baseline_from_report, diff
from riskratchet.models import DiffReport, DiffStatus, RiskReport

UNLABELED = "unlabeled"


@dataclass(frozen=True)
class RegressedFn:
    target: str  # "path::qualname"
    base_score: float
    head_score: float
    delta: float


@dataclass(frozen=True)
class OutcomeRecord:
    repo: str
    pr: int
    base_sha: str
    head_sha: str
    regression_count: int
    component_regression_count: int
    moved_count: int
    ambiguous_rename_count: int
    new_count: int
    regressed: tuple[RegressedFn, ...]
    match_confidences: tuple[float, ...]
    label: str = UNLABELED
    label_stale: bool = False
    # Filled by coverage_replay; defaults keep the in-process core testable.
    base_tests_failed: int | None = None
    head_tests_failed: int | None = None
    base_usable_coverage: bool = True
    head_usable_coverage: bool = True

    def to_digest(self) -> dict[str, object]:
        """Churn-resistant view for the committed rollup.

        Keyed on short SHAs, floats rounded, lists sorted. This makes a record
        byte-identical across re-runs *only when the inputs are fixed*: the same
        base/head SHAs (so `pr-labels.toml` must pin them — `gh pr list` returns
        whatever is merged now) and a deterministic test suite (flaky or
        time-dependent coverage will shift scores). It is not reproducible from
        the SHAs alone.
        """
        return {
            "repo": self.repo,
            "pr": self.pr,
            "base_sha": self.base_sha[:12],
            "head_sha": self.head_sha[:12],
            "label": self.label,
            "label_stale": self.label_stale,
            "regression_count": self.regression_count,
            "component_regression_count": self.component_regression_count,
            "moved_count": self.moved_count,
            "ambiguous_rename_count": self.ambiguous_rename_count,
            "new_count": self.new_count,
            "regressed": [
                {
                    "target": r.target,
                    "base_score": round(r.base_score, 2),
                    "head_score": round(r.head_score, 2),
                    "delta": round(r.delta, 2),
                }
                for r in sorted(self.regressed, key=lambda r: r.target)
            ],
            "match_confidences": sorted(round(c, 3) for c in self.match_confidences),
            "base_usable_coverage": self.base_usable_coverage,
            "head_usable_coverage": self.head_usable_coverage,
            "base_tests_failed": self.base_tests_failed,
            "head_tests_failed": self.head_tests_failed,
        }


@dataclass
class _Tally:
    regressed: list[RegressedFn] = field(default_factory=list)
    component_regressed: int = 0
    moved: int = 0
    ambiguous: int = 0
    new: int = 0
    confidences: list[float] = field(default_factory=list)


def _tally(report: DiffReport) -> _Tally:
    t = _Tally()
    for entry in report.entries:
        if entry.status is DiffStatus.REGRESSED:
            base = entry.previous_score or 0.0
            head = entry.current_score or 0.0
            t.regressed.append(
                RegressedFn(
                    target=entry.id.as_target(),
                    base_score=base,
                    head_score=head,
                    delta=entry.delta if entry.delta is not None else head - base,
                )
            )
        elif entry.status is DiffStatus.COMPONENT_REGRESSED:
            t.component_regressed += 1
        elif entry.status is DiffStatus.MOVED:
            t.moved += 1
            if entry.match_confidence is not None:
                t.confidences.append(entry.match_confidence)
        elif entry.status is DiffStatus.AMBIGUOUS_RENAME:
            t.ambiguous += 1
            if entry.match_confidence is not None:
                t.confidences.append(entry.match_confidence)
        elif entry.status is DiffStatus.NEW:
            t.new += 1
    return t


def replay_reports(
    *,
    repo: str,
    pr: int,
    base_sha: str,
    head_sha: str,
    base_report: RiskReport,
    head_report: RiskReport,
    fail_regression_above: float = 5.0,
    fail_component_regression_above: float = 15.0,
) -> OutcomeRecord:
    """Diff an already-analyzed head report against a base report."""
    baseline = baseline_from_report(base_report)
    report = diff(
        head_report,
        baseline,
        fail_regression_above=fail_regression_above,
        fail_component_regression_above=fail_component_regression_above,
    )
    t = _tally(report)
    return OutcomeRecord(
        repo=repo,
        pr=pr,
        base_sha=base_sha,
        head_sha=head_sha,
        regression_count=len(t.regressed),
        component_regression_count=t.component_regressed,
        moved_count=t.moved,
        ambiguous_rename_count=t.ambiguous,
        new_count=t.new,
        regressed=tuple(t.regressed),
        match_confidences=tuple(t.confidences),
    )


def replay_paths(
    *,
    repo: str,
    pr: int,
    base_sha: str,
    head_sha: str,
    base_paths: list[Path],
    base_root: Path,
    head_paths: list[Path],
    head_root: Path,
    base_coverage: Path | None = None,
    head_coverage: Path | None = None,
    fail_regression_above: float = 5.0,
) -> OutcomeRecord:
    """Analyze two source trees (with optional coverage) and diff head vs base."""
    base_report = analyze_report(base_paths, base_root, coverage_path=base_coverage)
    head_report = analyze_report(head_paths, head_root, coverage_path=head_coverage)
    return replay_reports(
        repo=repo,
        pr=pr,
        base_sha=base_sha,
        head_sha=head_sha,
        base_report=base_report,
        head_report=head_report,
        fail_regression_above=fail_regression_above,
    )


def join_label(record: OutcomeRecord, labels: list[PrLabel]) -> OutcomeRecord:
    """Attach a manual label by exact (repo, pr, base_sha, head_sha) match.

    A label for the same (repo, pr) but different SHAs is treated as **stale**
    (the PR was rebased since labeling): the record stays ``unlabeled`` but is
    flagged so a reader can re-pin it. Unlabeled records are still captured.
    """
    key = (record.repo, record.pr, record.base_sha, record.head_sha)
    by_key = {label.key: label for label in labels}
    exact = by_key.get(key)
    if exact is not None:
        return _with_label(record, exact.label, stale=False)
    has_pr_label = any(label.repo == record.repo and label.pr == record.pr for label in labels)
    return _with_label(record, UNLABELED, stale=has_pr_label)


def _with_label(record: OutcomeRecord, label: str, *, stale: bool) -> OutcomeRecord:
    return OutcomeRecord(
        repo=record.repo,
        pr=record.pr,
        base_sha=record.base_sha,
        head_sha=record.head_sha,
        regression_count=record.regression_count,
        component_regression_count=record.component_regression_count,
        moved_count=record.moved_count,
        ambiguous_rename_count=record.ambiguous_rename_count,
        new_count=record.new_count,
        regressed=record.regressed,
        match_confidences=record.match_confidences,
        label=label,
        label_stale=stale,
        base_tests_failed=record.base_tests_failed,
        head_tests_failed=record.head_tests_failed,
        base_usable_coverage=record.base_usable_coverage,
        head_usable_coverage=record.head_usable_coverage,
    )
