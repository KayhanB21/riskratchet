"""Windowed function-edit counting — the change-proneness primitive.

For each commit in a window, find which functions it edits (NEW-side changed ranges →
functions parsed at that commit → span overlap) and attribute the edit back to a
snapshot-`S` function via the SZZ tracker. Used both directions:

* forward `(S, HEAD]` → the **change-proneness label** (future maintenance burden);
* backward `(S-window, S]` -> the **past-churn null feature** (prior activity).

Pure git history, coverage-independent. Reuses `szz.functions_at_revision`,
`defects.track_to_snapshot`, and the shared `git()` wrapper. The NEW-side hunk parser
mirrors `szz._parse_old_hunks` (which is old-side, for blame); here we want the lines a
commit *added/changed* so we can find what it touched.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from bin.calibration.defects import SnapshotPopulation, track_to_snapshot
from bin.calibration.git_checkout import CommandRunner, default_runner, git
from bin.calibration.szz import Implication, functions_at_revision
from riskratchet.baseline import baseline_from_report
from riskratchet.models import FunctionId

# New-side hunk: `@@ -old_start[,old_len] +new_start[,new_len] @@`.
_NEW_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

# (commits_touching, total_lines_changed) for one function over a window.
ChangeCount = tuple[int, int]


def _normalize_new_path(path: str) -> str | None:
    """New-side (`+++ b/...`) path; None for /dev/null (deleted file)."""
    path = path.strip()
    if path == "/dev/null":
        return None
    if path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    if path.startswith("b/"):
        return path[2:]
    return path


def _parse_new_hunks(diff_text: str) -> list[tuple[str, int, int]]:
    """New-side changed ranges from a `git show --unified=0` diff.

    Skips pure-deletion hunks (new_len == 0) — nothing on the new side to attribute.
    Returns (new_path, start, end) line ranges in the post-commit file.
    """
    ranges: list[tuple[str, int, int]] = []
    current: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            current = _normalize_new_path(line[4:])
            continue
        if line.startswith("--- "):
            continue
        match = _NEW_HUNK_RE.match(line)
        if match is None or current is None:
            continue
        start = int(match.group(1))
        count = int(match.group(2) or "1")
        if count == 0:
            continue
        ranges.append((current, start, start + count - 1))
    return ranges


def _pathspec(paths: tuple[str, ...]) -> list[str]:
    return ["--", *paths] if paths else []


def changed_ranges_for_commit(
    clone: Path, sha: str, paths: tuple[str, ...], *, run: CommandRunner = default_runner
) -> list[tuple[str, int, int]]:
    proc = git(
        ["show", "--format=", "--unified=0", "--no-ext-diff", "-w", sha, *_pathspec(paths)],
        clone,
        run=run,
    )
    if proc.returncode != 0:
        return []
    return _parse_new_hunks(proc.stdout)


def _overlaps(a0: int, a1: int, b0: int, b1: int) -> bool:
    return a0 <= b1 and b0 <= a1


def commits_in_range(
    clone: Path,
    since_sha: str,
    until_sha: str,
    paths: tuple[str, ...],
    *,
    run: CommandRunner = default_runner,
) -> list[str]:
    """SHAs in `(since_sha, until_sha]` touching `paths` (newest first)."""
    proc = git(["log", f"{since_sha}..{until_sha}", "--format=%H", *_pathspec(paths)], clone, run=run)
    if proc.returncode != 0:
        return []
    return [line for line in proc.stdout.split() if line]


def _commit_date(clone: Path, sha: str, *, run: CommandRunner) -> str:
    proc = git(["show", "-s", "--format=%cI", sha], clone, run=run)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def past_window_start(
    clone: Path, snapshot_sha: str, window_days: int, *, run: CommandRunner = default_runner
) -> str | None:
    """The commit at-or-before `(S_date - window_days)`, or None if the repo lacks that
    much history before `S` (i.e. the past window would be short, the repo is too young)."""
    iso = _commit_date(clone, snapshot_sha, run=run)
    if not iso:
        return None
    # Python 3.10's fromisoformat() rejects a trailing 'Z'; normalize to a numeric
    # offset so the 3.10 CI job parses git's date the same as 3.11+.
    cutoff = (datetime.fromisoformat(iso.replace("Z", "+00:00")) - timedelta(days=window_days)).isoformat()
    proc = git(["rev-list", "-1", f"--before={cutoff}", snapshot_sha], clone, run=run)
    return proc.stdout.strip() or None


def count_changes(
    clone: Path,
    snapshot: SnapshotPopulation,
    commits: list[str],
    paths: tuple[str, ...],
    *,
    run: CommandRunner = default_runner,
) -> dict[FunctionId, ChangeCount]:
    """Attribute each commit's edits to the snapshot-`S` function it touched.

    Per commit: NEW-side changed ranges → functions parsed at that commit → overlap →
    `track_to_snapshot` back to `S` (exact id, else fingerprint). A function is counted at
    most once per commit (de-duped), with its changed-line tally summed.
    """
    baseline = baseline_from_report(snapshot.report)
    acc: dict[FunctionId, list[int]] = {}
    for sha in commits:
        ranges = changed_ranges_for_commit(clone, sha, paths, run=run)
        by_path: dict[str, list[tuple[int, int]]] = {}
        for path, start, end in ranges:
            by_path.setdefault(path, []).append((start, end))
        touched: dict[FunctionId, int] = {}  # S FunctionId -> lines changed in this commit
        for path, rngs in by_path.items():
            fns = functions_at_revision(clone, sha, path, run=run)
            if not fns:
                continue
            for start, end in rngs:
                for fn in fns:
                    if not _overlaps(start, end, fn.span.start_line, fn.span.end_line):
                        continue
                    sid = track_to_snapshot(
                        Implication(fix_sha=sha, introducer_sha=sha, parent_fn_id=fn.id, parent_fn=fn),
                        snapshot,
                        baseline=baseline,
                    )
                    if sid is None:
                        continue
                    touched[sid] = touched.get(sid, 0) + (end - start + 1)
        for sid, lines in touched.items():
            entry = acc.setdefault(sid, [0, 0])
            entry[0] += 1
            entry[1] += lines
    return {fid: (entry[0], entry[1]) for fid, entry in acc.items()}
