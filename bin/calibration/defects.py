"""Build the SZZ defect-implication label for the functions at a snapshot S.

Ties together the pieces: score every function at S (reusing the phase-1
full-coverage `replay_revision`), mine fixes in (S, HEAD], blame them to the
introducing function at each fix's parent, then track that function back to an S
function (exact id, else fingerprint/signature via `match_rename`). The result is
a per-function defect count keyed by the S `FunctionId`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from bin.calibration.config import RepoConfig
from bin.calibration.coverage_replay import replay_revision
from bin.calibration.fixes import DEFAULT_FIX_KEYWORDS, mine_fix_commits
from bin.calibration.git_checkout import CommandRunner, default_runner, ensure_clone, git
from bin.calibration.szz import Implication, implications_for_fix
from riskratchet.analysis import DiscoveredFunction
from riskratchet.baseline import baseline_from_report
from riskratchet.matching import MATCH_THRESHOLD, match_rename
from riskratchet.models import (
    Baseline,
    BaselineEntry,
    ChurnStats,
    ComplexityStats,
    CoverageStats,
    FileStats,
    FunctionId,
    FunctionRisk,
    RiskComponents,
    RiskReport,
)


@dataclass(frozen=True)
class SnapshotPopulation:
    snapshot_sha: str
    report: RiskReport


@dataclass
class DefectLabels:
    repo: str
    snapshot_sha: str
    head_sha: str
    window_days: int
    n_functions: int
    n_fixes_scanned: int
    n_fixes_blamed: int
    n_implications_untracked: int
    counts: dict[FunctionId, int] = field(default_factory=dict)  # defect functions only

    @property
    def n_defect_functions(self) -> int:
        return len(self.counts)


def _tail(qualname: str) -> str:
    return qualname.rsplit(".", 1)[-1]


def _synthetic_fn(fn: DiscoveredFunction) -> FunctionRisk:
    """Wrap a DiscoveredFunction as a FunctionRisk for `match_rename`.

    Only id / span / fingerprint / signature carry signal; components and score
    are zeroed (we have no scored parent-revision report), so the match rides on
    body fingerprint + signature + path + qualname-tail — the intended
    track-a-moved-function path.
    """
    return FunctionRisk(
        id=fn.id,
        span=fn.span,
        is_public=fn.is_public,
        complexity=ComplexityStats(cyclomatic=0),
        coverage=CoverageStats.uncovered(),
        churn=ChurnStats(commits=0),
        file_stats=FileStats(path=fn.id.path, total_lines=0, function_count=0),
        components=RiskComponents(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        score=0.0,
        crap=0.0,
        fingerprint=fn.fingerprint,
        signature=fn.signature,
    )


def track_to_snapshot(
    impl: Implication, snapshot: SnapshotPopulation, *, baseline: Baseline | None = None
) -> FunctionId | None:
    """Map an implicated function at <fix>^ back to a snapshot-S FunctionId."""
    report = snapshot.report
    if impl.parent_fn_id in report.by_id():
        return impl.parent_fn_id
    entries = (baseline or baseline_from_report(report)).entries
    pf = impl.parent_fn
    tail = _tail(pf.id.qualname)
    candidates: list[BaselineEntry] = [
        e
        for e in entries.values()
        if e.fingerprint == pf.fingerprint or e.signature == pf.signature or _tail(e.id.qualname) == tail
    ]
    if not candidates:
        return None
    result = match_rename(_synthetic_fn(pf), candidates)
    if result.previous is not None and not result.is_ambiguous and result.confidence >= MATCH_THRESHOLD:
        return result.previous.id
    return None


def build_labels(
    repo_name: str,
    snapshot: SnapshotPopulation,
    implications: list[Implication],
    *,
    head_sha: str,
    window_days: int,
    n_fixes_scanned: int,
    n_fixes_blamed: int,
) -> DefectLabels:
    baseline = baseline_from_report(snapshot.report)
    fixes_by_fn: dict[FunctionId, set[str]] = {}
    untracked = 0
    for impl in implications:
        sid = track_to_snapshot(impl, snapshot, baseline=baseline)
        if sid is None:
            untracked += 1
            continue
        fixes_by_fn.setdefault(sid, set()).add(impl.fix_sha)
    return DefectLabels(
        repo=repo_name,
        snapshot_sha=snapshot.snapshot_sha,
        head_sha=head_sha,
        window_days=window_days,
        n_functions=len(snapshot.report.functions),
        n_fixes_scanned=n_fixes_scanned,
        n_fixes_blamed=n_fixes_blamed,
        n_implications_untracked=untracked,
        counts={fid: len(shas) for fid, shas in fixes_by_fn.items()},
    )


def score_snapshot(
    repo: RepoConfig, snapshot_sha: str, *, run: CommandRunner = default_runner
) -> SnapshotPopulation | None:
    """Full-coverage score of every function at S, reusing the phase-1 replay."""
    result = replay_revision(repo, snapshot_sha, run=run)
    if result.report is None:
        return None
    return SnapshotPopulation(snapshot_sha=snapshot_sha, report=result.report)


def resolve_snapshot(
    clone: Path,
    repo: RepoConfig,
    *,
    snapshot_sha_override: str = "",
    snapshot_days: int,
    run: CommandRunner = default_runner,
) -> str | None:
    """Pin (override > config) or derive S from `--before=<days>.days.ago`."""
    if snapshot_sha_override:
        return snapshot_sha_override
    if repo.snapshot_sha:
        return repo.snapshot_sha
    proc = git(["rev-list", "-1", f"--before={snapshot_days}.days.ago", repo.pr_branch], clone, run=run)
    return proc.stdout.strip() or None


def collect_defect_labels(
    repo: RepoConfig,
    *,
    snapshot_sha_override: str = "",
    snapshot_days: int = 365,
    window_days: int = 365,
    max_fixes: int = 100,
    run: CommandRunner = default_runner,
) -> tuple[SnapshotPopulation | None, DefectLabels | None]:
    """End-to-end per-repo defect labelling. Returns (snapshot, labels) or (None, None)."""
    clone = ensure_clone(repo, run=run)
    if clone is None:
        return None, None
    snapshot_sha = resolve_snapshot(
        clone, repo, snapshot_sha_override=snapshot_sha_override, snapshot_days=snapshot_days, run=run
    )
    if snapshot_sha is None:
        return None, None
    head_sha = git(["rev-parse", repo.pr_branch], clone, run=run).stdout.strip() or repo.pr_branch
    snapshot = score_snapshot(repo, snapshot_sha, run=run)
    if snapshot is None:
        return None, None

    paths = tuple(repo.paths)
    keywords = repo.fix_keywords or DEFAULT_FIX_KEYWORDS
    fixes = mine_fix_commits(
        clone, since_sha=snapshot_sha, until_sha=head_sha, paths=paths, keywords=keywords, run=run
    )
    blamable = [f for f in fixes if not f.is_merge][:max_fixes]
    ignore = Path(repo.ignore_revs_file) if repo.ignore_revs_file else None
    if ignore is not None and not ignore.exists():
        ignore = None
    implications: list[Implication] = []
    for fix in blamable:
        implications.extend(implications_for_fix(clone, fix.sha, paths, ignore_revs_file=ignore, run=run))
    labels = build_labels(
        repo.name,
        snapshot,
        implications,
        head_sha=head_sha,
        window_days=window_days,
        n_fixes_scanned=len(fixes),
        n_fixes_blamed=len(blamable),
    )
    return snapshot, labels
