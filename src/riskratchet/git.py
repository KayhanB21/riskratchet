"""Optional churn provider backed by `git log`.

Churn is attributed to each function's current source span. If git is not
present or the directory is not a repo, all functions report zero churn and
the churn component drops out of scoring naturally.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

from riskratchet.models import ChurnStats, FunctionId, FunctionSpan

DEFAULT_CHURN_WINDOW_DAYS = 90
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

FunctionChurnTarget = tuple[FunctionId, FunctionSpan]


def collect_function_churn(
    root: Path,
    functions: Sequence[FunctionChurnTarget],
    *,
    days: int = DEFAULT_CHURN_WINDOW_DAYS,
    enabled: bool = True,
) -> dict[FunctionId, ChurnStats]:
    """Return churn counts keyed by function id for the current function spans.

    The implementation parses zero-context diffs for commits in the churn
    window and counts a commit for a function when any changed current-file
    line overlaps that function's current start/end line range. Any git
    failure collapses to an empty mapping; callers treat missing ids as zero.
    """
    if not enabled or not functions:
        return {}
    if not (root / ".git").exists():
        return {}

    targets_by_path: dict[str, list[FunctionChurnTarget]] = {}
    for function_id, span in functions:
        targets_by_path.setdefault(function_id.path, []).append((function_id, span))
    paths = sorted(targets_by_path)

    commits = _commits_touching_paths(root, paths, days)
    if not commits:
        return {}

    seen_by_function: dict[FunctionId, set[str]] = {function_id: set() for function_id, _ in functions}
    for commit in commits:
        for path, start, end in _changed_ranges_for_commit(root, commit, paths):
            for function_id, span in targets_by_path.get(path, []):
                if _overlaps(start, end, span.start_line, span.end_line):
                    seen_by_function[function_id].add(commit)

    return {
        function_id: ChurnStats(commits=len(commits_for_function))
        for function_id, commits_for_function in seen_by_function.items()
        if commits_for_function
    }


def collect_file_churn(
    root: Path,
    *,
    days: int = DEFAULT_CHURN_WINDOW_DAYS,
    enabled: bool = True,
) -> dict[str, int]:
    """Return `{posix_relative_path: commit_count}` for the churn window.

    A single `git log` invocation is used so the cost stays bounded even for
    large repositories. Any failure (no git, no .git, timeout) collapses to
    an empty mapping; callers treat that as "no churn data".
    """
    if not enabled:
        return {}
    if not (root / ".git").exists():
        return {}
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "log",
                f"--since={days}.days.ago",
                "--name-only",
                "--pretty=format:",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}

    counts: dict[str, int] = {}
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        counts[line] = counts.get(line, 0) + 1
    return counts


def churn_for_file(churn_by_path: dict[str, int], relative_path: str) -> ChurnStats:
    return ChurnStats(commits=churn_by_path.get(relative_path, 0))


def churn_for_function(
    churn_by_function: dict[FunctionId, ChurnStats],
    function_id: FunctionId,
) -> ChurnStats:
    return churn_by_function.get(function_id, ChurnStats(commits=0))


def _commits_touching_paths(root: Path, paths: Sequence[str], days: int) -> list[str]:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "log",
                f"--since={days}.days.ago",
                "--format=%H",
                "--",
                *paths,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _changed_ranges_for_commit(
    root: Path,
    commit: str,
    paths: Sequence[str],
) -> list[tuple[str, int, int]]:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "show",
                "--format=",
                "--unified=0",
                "--no-ext-diff",
                commit,
                "--",
                *paths,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []

    ranges: list[tuple[str, int, int]] = []
    current_path: str | None = None
    for raw_line in result.stdout.splitlines():
        if raw_line.startswith("+++ "):
            current_path = _normalize_diff_path(raw_line[4:])
            continue
        match = _HUNK_RE.match(raw_line)
        if match is None or current_path is None:
            continue
        start = int(match.group(1))
        count = int(match.group(2) or "1")
        end = start if count == 0 else start + count - 1
        ranges.append((current_path, start, end))
    return ranges


def _normalize_diff_path(path: str) -> str | None:
    path = path.strip()
    if path == "/dev/null":
        return None
    if path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    if path.startswith("b/"):
        return path[2:]
    return path


def _overlaps(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    return start_a <= end_b and start_b <= end_a
