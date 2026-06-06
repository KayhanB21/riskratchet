"""SZZ core: from a bug-fix commit to the function(s) that introduced the bug.

Given a fix commit, find the lines it *deleted/modified* (the buggy lines as they
existed at the fix's parent), `git blame` them to the introducing commits, and map
each blamed line to the enclosing function at the parent revision. Reuses
riskratchet's diff-path normalization shape, `parse_file` for line→function
mapping, and the shared `git()` wrapper. Pure parsers (`_parse_old_hunks`,
`_parse_blame_porcelain`) are split out so they can be unit-tested without git.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from bin.calibration.git_checkout import CommandRunner, default_runner, git
from riskratchet.analysis import DiscoveredFunction, ParseError, parse_file
from riskratchet.models import FunctionId

# Old-side hunk: `@@ -old_start[,old_len] +new_start[,new_len] @@`.
_OLD_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@")
# A git-blame --porcelain group header: `<40-hex> <orig_lno> <final_lno>[ <count>]`.
_BLAME_HEADER_RE = re.compile(r"^([0-9a-f]{40}) \d+ (\d+)(?: \d+)?$")


@dataclass(frozen=True)
class BlameLine:
    introducer_sha: str
    lineno_at_parent: int


@dataclass(frozen=True)
class Implication:
    fix_sha: str
    introducer_sha: str
    parent_fn_id: FunctionId
    parent_fn: DiscoveredFunction


def _normalize_old_path(path: str) -> str | None:
    """Old-side (`--- a/...`) path normalization; None for /dev/null (added file)."""
    path = path.strip()
    if path == "/dev/null":
        return None
    if path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
    if path.startswith("a/"):
        return path[2:]
    return path


def _parse_old_hunks(diff_text: str) -> list[tuple[str, int, int]]:
    """Old-side changed ranges from a `git show --unified=0` diff.

    Skips pure-addition hunks (old_len == 0) — a new line has no prior author to
    blame. Returns (old_path, start, end) line ranges in the parent revision.
    """
    ranges: list[tuple[str, int, int]] = []
    current: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("--- "):
            current = _normalize_old_path(line[4:])
            continue
        if line.startswith("+++ "):
            continue
        match = _OLD_HUNK_RE.match(line)
        if match is None or current is None:
            continue
        start = int(match.group(1))
        count = int(match.group(2) or "1")
        if count == 0:
            continue
        ranges.append((current, start, start + count - 1))
    return ranges


def _parse_blame_porcelain(text: str) -> list[BlameLine]:
    """One BlameLine per blamed line: (introducing sha, line number at parent)."""
    out: list[BlameLine] = []
    for line in text.splitlines():
        match = _BLAME_HEADER_RE.match(line)
        if match is not None:
            out.append(BlameLine(introducer_sha=match.group(1), lineno_at_parent=int(match.group(2))))
    return out


def deleted_ranges_for_commit(
    clone: Path, fix_sha: str, paths: tuple[str, ...], *, run: CommandRunner = default_runner
) -> list[tuple[str, int, int]]:
    proc = git(
        ["show", "--format=", "--unified=0", "--no-ext-diff", "-w", fix_sha, "--", *paths],
        clone,
        run=run,
    )
    if proc.returncode != 0:
        return []
    return _parse_old_hunks(proc.stdout)


def blame_introducers(
    clone: Path,
    fix_sha: str,
    path: str,
    start: int,
    end: int,
    *,
    ignore_revs_file: Path | None = None,
    run: CommandRunner = default_runner,
) -> list[BlameLine]:
    """Blame lines [start, end] at `<fix>^` to find the introducing commits."""
    argv = ["blame", "-w", "-L", f"{start},{end}", "--porcelain"]
    if ignore_revs_file is not None:
        argv += ["--ignore-revs-file", str(ignore_revs_file)]
    argv += [f"{fix_sha}^", "--", path]
    proc = git(argv, clone, run=run)
    if proc.returncode != 0:
        return []
    return _parse_blame_porcelain(proc.stdout)


def file_at_revision(clone: Path, sha: str, path: str, *, run: CommandRunner = default_runner) -> str | None:
    proc = git(["show", f"{sha}:{path}"], clone, run=run)
    return proc.stdout if proc.returncode == 0 else None


def functions_at_revision(
    clone: Path, sha: str, path: str, *, run: CommandRunner = default_runner
) -> tuple[DiscoveredFunction, ...]:
    """Parse the file as it existed at `sha` and return its functions with spans."""
    content = file_at_revision(clone, sha, path, run=run)
    if content is None:
        return ()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        parsed = parse_file(target, root=root)
    if isinstance(parsed, ParseError):
        return ()
    return parsed.functions


def _function_for_line(fns: tuple[DiscoveredFunction, ...], line: int) -> DiscoveredFunction | None:
    for fn in fns:
        if fn.span.start_line <= line <= fn.span.end_line:
            return fn
    return None


def implications_for_fix(
    clone: Path,
    fix_sha: str,
    paths: tuple[str, ...],
    *,
    ignore_revs_file: Path | None = None,
    run: CommandRunner = default_runner,
) -> list[Implication]:
    """Full SZZ for one fix: deleted lines → blame → enclosing function at parent."""
    ranges = deleted_ranges_for_commit(clone, fix_sha, paths, run=run)
    by_path: dict[str, list[tuple[int, int]]] = {}
    for path, start, end in ranges:
        by_path.setdefault(path, []).append((start, end))

    impls: list[Implication] = []
    for path, rngs in by_path.items():
        fns = functions_at_revision(clone, f"{fix_sha}^", path, run=run)
        if not fns:
            continue
        for start, end in rngs:
            for blame in blame_introducers(
                clone, fix_sha, path, start, end, ignore_revs_file=ignore_revs_file, run=run
            ):
                fn = _function_for_line(fns, blame.lineno_at_parent)
                if fn is None:
                    continue
                impls.append(
                    Implication(
                        fix_sha=fix_sha,
                        introducer_sha=blame.introducer_sha,
                        parent_fn_id=fn.id,
                        parent_fn=fn,
                    )
                )
    return impls
