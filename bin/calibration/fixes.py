"""Mine bug-fix commits from a repo's history (heuristic, the standard SZZ step).

Classifies a commit as a fix when its subject matches any keyword at a word
boundary (so "fix" matches fix/fixes/fixed/fixing but not "prefix"). Merge commits
are flagged (`is_merge`) so the caller can exclude them from blame — a merge has no
single old side to attribute. Pure git via the shared `git()` wrapper; no `gh`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from bin.calibration.git_checkout import CommandRunner, default_runner, git

# Word-boundary, prefix-allowed: \bfix matches fix/fixes/fixed/fixing, not "prefix".
DEFAULT_FIX_KEYWORDS: tuple[str, ...] = ("fix", "bug", "close", "resolve", "hotfix")

_ISSUE_RE = re.compile(r"#(\d+)")
# Field/record separators embedded in the git log format.
_FMT = "%H%x00%P%x00%s"


@dataclass(frozen=True)
class FixCommit:
    sha: str
    subject: str
    issues: tuple[int, ...]
    is_merge: bool


def _keyword_regex(keywords: tuple[str, ...]) -> re.Pattern[str]:
    alternation = "|".join(re.escape(k) for k in keywords)
    return re.compile(rf"\b(?:{alternation})", re.IGNORECASE)


def is_fix_subject(subject: str, keywords: tuple[str, ...] = DEFAULT_FIX_KEYWORDS) -> bool:
    return _keyword_regex(keywords).search(subject) is not None


def parse_log(stdout: str, keywords: tuple[str, ...] = DEFAULT_FIX_KEYWORDS) -> list[FixCommit]:
    """Parse NUL-delimited `git log --format=%H\\0%P\\0%s` output into fix commits."""
    fixes: list[FixCommit] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\x00")
        if len(parts) != 3:
            continue
        sha, parents, subject = parts
        if not is_fix_subject(subject, keywords):
            continue
        issues = tuple(int(n) for n in _ISSUE_RE.findall(subject))
        is_merge = len(parents.split()) > 1
        fixes.append(FixCommit(sha=sha, subject=subject, issues=issues, is_merge=is_merge))
    return fixes


def mine_fix_commits(
    clone: Path,
    *,
    since_sha: str,
    until_sha: str,
    paths: tuple[str, ...],
    keywords: tuple[str, ...] = DEFAULT_FIX_KEYWORDS,
    run: CommandRunner = default_runner,
) -> list[FixCommit]:
    """Fix commits in (since_sha, until_sha] touching `paths` (empty paths = all)."""
    argv = ["log", f"{since_sha}..{until_sha}", f"--format={_FMT}"]
    if paths:
        argv += ["--", *paths]
    proc = git(argv, clone, run=run)
    if proc.returncode != 0:
        return []
    return parse_log(proc.stdout, keywords)
