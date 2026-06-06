"""Enumerate recent merged PRs for a corpus repo via the ``gh`` CLI.

Dev-only: ``gh`` is the sanctioned GitHub tool, never a riskratchet runtime
dependency. If ``gh`` is missing or unauthenticated the enumeration degrades to an
empty list with a stderr note, so the rest of the harness (e.g. re-scoring already
cached records) still runs offline.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

from bin.calibration.config import RepoConfig

# A command runner: argv -> stdout. Injectable so tests never shell out to gh.
Runner = Callable[[list[str]], str]


@dataclass(frozen=True)
class PrRef:
    repo: str  # corpus config name
    number: int
    base_sha: str  # base branch tip at merge (refined to merge-base at checkout time)
    head_sha: str
    merge_commit: str


def repo_slug(url: str) -> str:
    """Derive ``owner/name`` from a GitHub clone URL."""
    trimmed = url.rstrip("/")
    if trimmed.endswith(".git"):
        trimmed = trimmed[: -len(".git")]
    parts = trimmed.split("/")
    if len(parts) < 2:
        raise ValueError(f"cannot derive owner/name from URL: {url!r}")
    return f"{parts[-2]}/{parts[-1]}"


def _default_runner(argv: list[str]) -> str:
    result = subprocess.run(argv, check=True, capture_output=True, text=True, timeout=120)
    return result.stdout


def enumerate_merged_prs(
    repo: RepoConfig,
    max_prs: int,
    *,
    runner: Runner = _default_runner,
) -> list[PrRef]:
    """Return up to ``max_prs`` recently-merged PRs targeting ``repo.pr_branch``.

    Returns ``[]`` (with a stderr note) when ``gh`` is unavailable, the call
    fails, or the JSON is malformed — never raises for those, so a missing ``gh``
    degrades gracefully.
    """
    slug = repo_slug(repo.url)
    argv = [
        "gh",
        "pr",
        "list",
        "--repo",
        slug,
        "--state",
        "merged",
        "--base",
        repo.pr_branch,
        "--limit",
        str(max_prs),
        "--json",
        "number,baseRefOid,headRefOid,mergeCommitOid",
    ]
    try:
        stdout = runner(argv)
    except FileNotFoundError:
        print(f"warning: gh not found; skipping PR enumeration for {repo.name}", file=sys.stderr)
        return []
    except subprocess.CalledProcessError as exc:
        print(f"warning: gh pr list failed for {repo.name}: {exc.stderr or exc}", file=sys.stderr)
        return []
    except subprocess.TimeoutExpired:
        print(f"warning: gh pr list timed out for {repo.name}", file=sys.stderr)
        return []

    return parse_pr_list(repo.name, stdout)


def parse_pr_list(repo_name: str, stdout: str) -> list[PrRef]:
    """Parse ``gh pr list --json`` output into ``PrRef`` records.

    Rows missing a base or head SHA (e.g. PRs from deleted forks) are skipped:
    they cannot be replayed.
    """
    try:
        rows = json.loads(stdout)
    except json.JSONDecodeError:
        print(f"warning: could not parse gh output for {repo_name}", file=sys.stderr)
        return []
    refs: list[PrRef] = []
    for row in rows:
        base = row.get("baseRefOid") or ""
        head = row.get("headRefOid") or ""
        if not base or not head:
            continue
        refs.append(
            PrRef(
                repo=repo_name,
                number=int(row["number"]),
                base_sha=base,
                head_sha=head,
                merge_commit=row.get("mergeCommitOid") or "",
            )
        )
    return refs
