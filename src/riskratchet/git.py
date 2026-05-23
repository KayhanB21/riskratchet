"""Optional churn provider backed by `git log`.

Per-line churn is expensive, so v1 attributes file-level churn (commits in
the last N days that touch the file) to every function in that file. If git
is not present or the directory is not a repo, all functions report zero
churn and the churn component drops out of scoring naturally.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from riskratchet.models import ChurnStats

DEFAULT_CHURN_WINDOW_DAYS = 90


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
