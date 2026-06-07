"""Change-proneness labels: a maintainability-flavoured outcome from git history.

For each function at a snapshot `S`, count how often it is edited in the future window
`(S, HEAD]` (the **label**) and in the past window `(S-window, S]` (the **null feature** —
prior activity). Binarize the label to "change-prone" = the within-repo top quartile of
future edit-count. Coverage-independent (pure git history); the function population is
scored coverage-free so untested repos are in scope. Reuses `defects.resolve_snapshot`,
`coverage_free.score_snapshot_coverage_free`, and `change_counting`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from bin.calibration.change_counting import (
    ChangeCount,
    commits_in_range,
    count_changes,
    past_window_start,
)
from bin.calibration.config import RepoConfig
from bin.calibration.coverage_free import score_snapshot_coverage_free
from bin.calibration.defects import SnapshotPopulation, resolve_snapshot
from bin.calibration.git_checkout import CommandRunner, default_runner, ensure_clone, git
from riskratchet.models import FunctionId

CHANGE_PRONE_QUANTILE = 0.25  # top quartile of future edit-count = change-prone


@dataclass
class PronenessLabels:
    repo: str
    snapshot_sha: str
    head_sha: str
    window_days: int
    n_functions: int
    n_future_commits: int
    insufficient_past_history: bool
    future: dict[FunctionId, ChangeCount] = field(default_factory=dict)
    past: dict[FunctionId, ChangeCount] = field(default_factory=dict)
    change_prone: set[FunctionId] = field(default_factory=set)

    @property
    def n_change_prone(self) -> int:
        return len(self.change_prone)


def _top_quartile(all_ids: list[FunctionId], future: dict[FunctionId, ChangeCount]) -> set[FunctionId]:
    """The most future-edited quarter of functions (excluding zero-activity ones)."""
    n = len(all_ids)
    if n == 0:
        return set()
    k = math.ceil(CHANGE_PRONE_QUANTILE * n)
    ranked = sorted(
        all_ids,
        key=lambda fid: (-future.get(fid, (0, 0))[0], -future.get(fid, (0, 0))[1], fid.as_target()),
    )
    return {fid for fid in ranked[:k] if future.get(fid, (0, 0))[0] > 0}


def build_proneness_labels(
    repo_name: str,
    snapshot: SnapshotPopulation,
    future: dict[FunctionId, ChangeCount],
    past: dict[FunctionId, ChangeCount],
    *,
    head_sha: str,
    window_days: int,
    n_future_commits: int,
    insufficient_past_history: bool,
) -> PronenessLabels:
    all_ids = [fn.id for fn in snapshot.report.functions]
    return PronenessLabels(
        repo=repo_name,
        snapshot_sha=snapshot.snapshot_sha,
        head_sha=head_sha,
        window_days=window_days,
        n_functions=len(all_ids),
        n_future_commits=n_future_commits,
        insufficient_past_history=insufficient_past_history,
        future=future,
        past=past,
        change_prone=_top_quartile(all_ids, future),
    )


def collect_proneness_labels(
    repo: RepoConfig,
    *,
    snapshot_sha_override: str = "",
    snapshot_days: int = 365,
    window_days: int = 365,
    run: CommandRunner = default_runner,
) -> tuple[SnapshotPopulation | None, PronenessLabels | None]:
    """End-to-end per-repo change-proneness labelling. Returns (snapshot, labels)."""
    clone = ensure_clone(repo, run=run)
    if clone is None:
        return None, None
    snapshot_sha = resolve_snapshot(
        clone, repo, snapshot_sha_override=snapshot_sha_override, snapshot_days=snapshot_days, run=run
    )
    if snapshot_sha is None:
        return None, None
    head_sha = git(["rev-parse", repo.pr_branch], clone, run=run).stdout.strip() or repo.pr_branch
    snapshot = score_snapshot_coverage_free(repo, snapshot_sha, run=run)
    if snapshot is None:
        return None, None

    paths = tuple(repo.paths)
    future_commits = commits_in_range(clone, snapshot_sha, head_sha, paths, run=run)
    future = count_changes(clone, snapshot, future_commits, paths, run=run)

    past_start = past_window_start(clone, snapshot_sha, window_days, run=run)
    if past_start is None:
        past: dict[FunctionId, ChangeCount] = {}
    else:
        past = count_changes(
            clone, snapshot, commits_in_range(clone, past_start, snapshot_sha, paths, run=run), paths, run=run
        )

    labels = build_proneness_labels(
        repo.name,
        snapshot,
        future,
        past,
        head_sha=head_sha,
        window_days=window_days,
        n_future_commits=len(future_commits),
        insufficient_past_history=past_start is None,
    )
    return snapshot, labels


def labels_to_dict(labels: PronenessLabels) -> dict[str, object]:
    """Churn-resistant: only functions with any past/future activity (or prone) are
    listed, sorted by target; everything absent is (past=0, future=0, not prone)."""
    ids = sorted(set(labels.future) | set(labels.past) | labels.change_prone, key=lambda fid: fid.as_target())
    rows = []
    for fid in ids:
        fc, fl = labels.future.get(fid, (0, 0))
        pc, pl = labels.past.get(fid, (0, 0))
        rows.append(
            {
                "target": fid.as_target(),
                "future_commits": fc,
                "future_lines": fl,
                "past_commits": pc,
                "past_lines": pl,
                "change_prone": fid in labels.change_prone,
            }
        )
    return {
        "snapshot_sha": labels.snapshot_sha,
        "head_sha": labels.head_sha,
        "window_days": labels.window_days,
        "n_functions": labels.n_functions,
        "n_change_prone": labels.n_change_prone,
        "n_future_commits": labels.n_future_commits,
        "insufficient_past_history": labels.insufficient_past_history,
        "functions": rows,
    }


def _as_int(value: object) -> int:
    assert isinstance(value, int)
    return value


def labels_from_dict(repo: str, data: dict[str, object]) -> PronenessLabels:
    rows = data["functions"]
    assert isinstance(rows, list)
    future: dict[FunctionId, ChangeCount] = {}
    past: dict[FunctionId, ChangeCount] = {}
    prone: set[FunctionId] = set()
    for row in rows:
        assert isinstance(row, dict)
        path, _, qualname = str(row["target"]).partition("::")
        fid = FunctionId(path, qualname)
        fc, fl = _as_int(row["future_commits"]), _as_int(row["future_lines"])
        pc, pl = _as_int(row["past_commits"]), _as_int(row["past_lines"])
        if fc or fl:
            future[fid] = (fc, fl)
        if pc or pl:
            past[fid] = (pc, pl)
        if bool(row["change_prone"]):
            prone.add(fid)
    return PronenessLabels(
        repo=repo,
        snapshot_sha=str(data["snapshot_sha"]),
        head_sha=str(data["head_sha"]),
        window_days=_as_int(data["window_days"]),
        n_functions=_as_int(data["n_functions"]),
        n_future_commits=_as_int(data["n_future_commits"]),
        insufficient_past_history=bool(data["insufficient_past_history"]),
        future=future,
        past=past,
        change_prone=prone,
    )
