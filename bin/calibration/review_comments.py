"""Construct anchor: mine "please split this / too complex" PR review comments.

Change-proneness is a *proxy* for maintainability. This module gives a small, direct
human-judgment signal to check it against: PR review comments that ask to split / simplify
/ refactor a function. We fetch review comments (`gh api .../pulls/<n>/comments`), classify
the body, map each to the function it sits on at the comment's commit, and track that back
to the snapshot `S`. The construct check (`flag_agreement_auc`) then asks: do human-flagged
functions have higher change-proneness than unflagged ones?

Honest limits (recorded in the findings doc): sparse, biased toward review-heavy repos (so
it lives on the *polished* end of the gradient — a validity check, not a population sample),
and line-numbers shift on rebase/squash. A directional anchor, never a primary label.
Human-run (needs `gh`); the test injects canned `gh` output + a local git repo.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from bin.calibration.defects import SnapshotPopulation, track_to_snapshot
from bin.calibration.git_checkout import CommandRunner, default_runner
from bin.calibration.predict import auc_from_mwu
from bin.calibration.proneness import PronenessLabels
from bin.calibration.prs import Runner, _default_runner, enumerate_merged_prs, repo_slug
from bin.calibration.szz import Implication, _function_for_line, functions_at_revision
from riskratchet.baseline import baseline_from_report
from riskratchet.models import FunctionId

# Maintainability-flavoured review asks. Deliberately narrow — false positives ("split
# view", "refactor the tests") are the main risk, so we lean toward precision.
_MAINTAINABILITY_RE = re.compile(
    r"\b(split (this|it|up)|extract (a|this|the)|too long|too complex|overly complex|"
    r"simplify (this|it)|hard to follow|break (this )?up|god (object|function|class)|"
    r"this (method|function|class) is (too )?(big|long|complex))\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ReviewComment:
    path: str
    line: int
    body: str
    commit_id: str


@dataclass
class ReviewFlags:
    repo: str
    snapshot_sha: str
    n_prs_scanned: int
    n_comments_scanned: int
    n_maintainability_comments: int
    counts: dict[FunctionId, int] = field(default_factory=dict)


def is_maintainability_comment(body: str) -> bool:
    return _MAINTAINABILITY_RE.search(body) is not None


def parse_review_comments(stdout: str) -> list[ReviewComment]:
    """Parse `gh api .../comments` JSON into mappable comments (those with a line + commit)."""
    try:
        rows = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    out: list[ReviewComment] = []
    for row in rows:
        path = row.get("path") or ""
        line = row.get("line") or row.get("original_line")
        commit = row.get("commit_id") or row.get("original_commit_id") or ""
        body = row.get("body") or ""
        if path and line and commit:
            out.append(ReviewComment(path=path, line=int(line), body=body, commit_id=commit))
    return out


def fetch_review_comments(
    slug: str, pr_number: int, *, runner: Runner = _default_runner
) -> list[ReviewComment]:
    argv = ["gh", "api", f"repos/{slug}/pulls/{pr_number}/comments", "--paginate"]
    try:
        stdout = runner(argv)
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        print(f"warning: gh api comments failed for {slug}#{pr_number}: {exc}", file=sys.stderr)
        return []
    return parse_review_comments(stdout)


def map_comment_to_function(
    clone: Path,
    comment: ReviewComment,
    snapshot: SnapshotPopulation,
    *,
    baseline: object | None = None,
    run: CommandRunner = default_runner,
) -> FunctionId | None:
    """The S function a comment sits on (parse the file at the comment's commit, find the
    enclosing function, track back to S). None if it maps to no tracked S function."""
    fns = functions_at_revision(clone, comment.commit_id, comment.path, run=run)
    fn = _function_for_line(fns, comment.line)
    if fn is None:
        return None
    impl = Implication(
        fix_sha=comment.commit_id, introducer_sha=comment.commit_id, parent_fn_id=fn.id, parent_fn=fn
    )
    return track_to_snapshot(impl, snapshot, baseline=baseline)  # type: ignore[arg-type]


def mine_review_flags(
    repo: object,
    clone: Path,
    snapshot: SnapshotPopulation,
    *,
    max_prs: int = 100,
    runner: Runner = _default_runner,
    run: CommandRunner = default_runner,
) -> ReviewFlags:
    """Mine maintainability review flags for one repo. `repo` is a RepoConfig."""
    slug = repo_slug(repo.url)  # type: ignore[attr-defined]
    baseline = baseline_from_report(snapshot.report)
    prs = enumerate_merged_prs(repo, max_prs, runner=runner)  # type: ignore[arg-type]
    counts: dict[FunctionId, int] = {}
    n_comments = 0
    n_maint = 0
    for pr in prs:
        for comment in fetch_review_comments(slug, pr.number, runner=runner):
            n_comments += 1
            if not is_maintainability_comment(comment.body):
                continue
            n_maint += 1
            sid = map_comment_to_function(clone, comment, snapshot, baseline=baseline, run=run)
            if sid is not None:
                counts[sid] = counts.get(sid, 0) + 1
    return ReviewFlags(
        repo=repo.name,  # type: ignore[attr-defined]
        snapshot_sha=snapshot.snapshot_sha,
        n_prs_scanned=len(prs),
        n_comments_scanned=n_comments,
        n_maintainability_comments=n_maint,
        counts=counts,
    )


def flag_agreement_auc(flags: ReviewFlags, labels: PronenessLabels) -> float:
    """Construct check: AUC of future change-proneness for human-flagged vs unflagged
    functions. >0.5 means flagged functions are indeed more change-prone (the proxy agrees
    with human judgment). NaN if either group is empty."""
    flagged = set(flags.counts)
    flagged_scores: list[float] = []
    unflagged_scores: list[float] = []
    for fid in flagged:
        flagged_scores.append(float(labels.future.get(fid, (0, 0))[0]))
    seen = flagged
    for fid, (commits, _lines) in labels.future.items():
        if fid not in seen:
            unflagged_scores.append(float(commits))
    # Unflagged functions with zero future activity are implicitly 0; include a baseline.
    if not unflagged_scores:
        unflagged_scores = [0.0]
    return auc_from_mwu(flagged_scores, unflagged_scores)


def flags_to_dict(flags: ReviewFlags) -> dict[str, object]:
    rows = [
        {"target": fid.as_target(), "flags": count}
        for fid, count in sorted(flags.counts.items(), key=lambda kv: kv[0].as_target())
    ]
    return {
        "snapshot_sha": flags.snapshot_sha,
        "n_prs_scanned": flags.n_prs_scanned,
        "n_comments_scanned": flags.n_comments_scanned,
        "n_maintainability_comments": flags.n_maintainability_comments,
        "n_flagged_functions": len(flags.counts),
        "functions": rows,
    }
