"""Optional churn provider backed by `git log`.

Churn is attributed to each function's current source span. If git is not
present or the directory is not a repo, all functions report zero churn and
the churn component drops out of scoring naturally.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from riskratchet._stderr import write_stderr
from riskratchet.models import ChurnStats, FunctionId, FunctionSpan

DEFAULT_CHURN_WINDOW_DAYS = 90
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

FunctionChurnTarget = tuple[FunctionId, FunctionSpan]


@dataclass(frozen=True, slots=True)
class RepoInfo:
    """Where the repository actually is, and whether it has full history."""

    root: Path
    shallow: bool


def repo_info(path: Path) -> RepoInfo | None:
    """Resolve the real repository for `path`, or `None` when there is none.

    One `git rev-parse` answers both questions, because both are wrong when asked of
    `path` directly: `.git` lives at the repository *top level*, so probing for it under a
    nested configuration directory reports "no repository" for a tree that is very much in
    one, and probing `.git/shallow` reports "full history" for the same reason — plus it is
    wrong again inside a worktree or submodule, where `.git` is a file.

    **This resolves the root; it does not yet score from it.** Churn in 0.3.7 is still
    collected relative to the configuration directory, deliberately: attributing it from the
    repository root would raise the churn component on nearly every function at once, and a
    patch release must not turn a green gate red. 0.4.0 makes that move, with the pathspecs
    re-anchored to match. What this buys now is that the tool can *say* churn is dead, and
    that the redaction salt and the shallow-clone warning stop being silently wrong.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel", "--is-shallow-repository"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    return RepoInfo(root=Path(lines[0]), shallow=len(lines) > 1 and lines[1] == "true")


def churn_root_mismatch(config_dir: Path) -> Path | None:
    """The repository root when it is not the directory churn is being collected from.

    `None` when they agree, or when there is no repository at all — the latter is already
    reported as "not a git repo" and does not need a second, more confusing message.
    """
    info = repo_info(config_dir)
    if info is None:
        return None
    try:
        same = info.root.resolve() == config_dir.resolve()
    except OSError:
        return None
    return None if same else info.root


def is_shallow_repo(root: Path) -> bool:
    """True when `root` is inside a shallow clone.

    Worth surfacing because the failure is silent: every git helper here
    collapses a failure to an empty result, and on a depth-1 clone
    `git log --since` simply sees one commit — so churn scores as zero rather
    than erroring. `actions/checkout` defaults to depth 1, which is how CI ends
    up disagreeing with a locally-generated baseline.

    Asks git rather than probing for `.git/shallow`, which answered "full history" for
    every nested configuration directory (the file is at the top level) and for every
    worktree and submodule (where `.git` is a file) — so the one warning that exists to
    catch a silent zero could not fire on the layouts most likely to produce one.
    """
    info = repo_info(root)
    return info is not None and info.shallow


ChurnErrorFn = Callable[[str], None] | None


class _ChurnFailures:
    """Collects git failures during one churn pass and reports them exactly once.

    Every helper below used to collapse a failure to an empty result, so a timeout, an
    unreadable repository, or a fork that could not allocate produced churn 0 for every
    function and said nothing — indistinguishable from "this code has not changed". A zero
    that is really an error is the worst kind, because it also writes itself into the next
    baseline as if it were a measurement.

    Reported once rather than per call: `_changed_ranges_for_commit` runs per commit, so a
    repository-wide failure would otherwise print one line per commit in the window.
    """

    def __init__(self, on_error: ChurnErrorFn) -> None:
        self._on_error = on_error
        self._seen: set[str] = set()

    def record(self, operation: str, exc: BaseException) -> None:
        reason = "timed out" if isinstance(exc, subprocess.TimeoutExpired) else str(exc)
        self.say(f"{operation} failed ({reason}); churn is scoring 0 for the functions it covers")

    def say(self, message: str) -> None:
        if message in self._seen:
            return
        self._seen.add(message)
        if self._on_error is not None:
            self._on_error(message)
        else:
            # No callback supplied (a direct library call). Still never silent, and the
            # same fallback `engine.py` uses for its coverage and parse warnings.
            write_stderr(f"warning: {message}")


def churn_is_available(root: Path, *, enabled: bool = True) -> bool:
    """Whether `collect_function_churn` can read any history for `root`.

    The same predicate `collect_function_churn` gates on, named once and exported so a
    report can record *why* every churn component is zero. A zero churn score otherwise
    means either "this function has not changed" or "there was no repository to ask",
    and a baseline written under the second reading gates against numbers that are not
    measurements. Keep the two in lockstep: this is the function to change when churn
    learns to resolve the real repository root.
    """
    return bool(enabled) and (root / ".git").exists()


def collect_function_churn(
    root: Path,
    functions: Sequence[FunctionChurnTarget],
    *,
    days: int = DEFAULT_CHURN_WINDOW_DAYS,
    enabled: bool = True,
    on_error: ChurnErrorFn = None,
) -> dict[FunctionId, ChurnStats]:
    """Return churn counts keyed by function id for the current function spans.

    The implementation parses zero-context diffs for commits in the churn
    window and counts a commit for a function when any changed current-file
    line overlaps that function's current start/end line range.

    A git failure still yields no churn for the functions it covers — there is nothing
    better to return — but `on_error` is told, once, so the caller can say the zero is an
    error rather than a measurement. Mirrors `coverage.load_coverage_map`'s `on_error`.
    """
    if not functions or not churn_is_available(root, enabled=enabled):
        return {}
    failures = _ChurnFailures(on_error)

    targets_by_path: dict[str, list[FunctionChurnTarget]] = {}
    for function_id, span in functions:
        targets_by_path.setdefault(function_id.path, []).append((function_id, span))
    paths = sorted(targets_by_path)

    commits = _commits_touching_paths(root, paths, days, failures)
    if not commits:
        return {}

    seen_by_function: dict[FunctionId, set[str]] = {function_id: set() for function_id, _ in functions}
    for commit in commits:
        for path, start, end in _changed_ranges_for_commit(root, commit, paths, failures):
            for function_id, span in targets_by_path.get(path, []):
                if _overlaps(start, end, span.start_line, span.end_line):
                    seen_by_function[function_id].add(commit)

    return {
        function_id: ChurnStats(commits=len(commits_for_function))
        for function_id, commits_for_function in seen_by_function.items()
        if commits_for_function
    }


def head_sha(root: Path) -> str | None:
    """Return the current HEAD commit SHA, or None if not a repo / git absent.

    Used to derive a default redaction salt so unsalted redaction is still
    unlinkable across commits. Any git failure collapses to None (the caller
    then warns and runs unsalted).

    Asks git rather than probing for `root/.git`, which is absent for every configuration
    directory below the repository top level — so a monorepo package silently fell back to
    *unsalted* redaction, the one outcome salting exists to prevent, while sitting in a
    repository with a perfectly good HEAD.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


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
    except (OSError, subprocess.TimeoutExpired):
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


def _commits_touching_paths(
    root: Path, paths: Sequence[str], days: int, failures: _ChurnFailures
) -> list[str]:
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
    except (OSError, subprocess.TimeoutExpired) as exc:
        failures.record("git log", exc)
        return []
    if result.returncode != 0:
        # A repository with no commits yet is not a failure — `git log` exits 128 with "does
        # not have any commits yet", and there is genuinely no churn to find. Warning there
        # would fire on every run in a freshly initialized repo, which is the kind of noise
        # that teaches people to ignore the warning that matters.
        if _has_commits(root):
            failures.say(f"git log exited {result.returncode}; churn is scoring 0 for every function")
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _has_commits(root: Path) -> bool:
    """Whether the repository has any commit at all, told apart from a git failure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _changed_ranges_for_commit(
    root: Path,
    commit: str,
    paths: Sequence[str],
    failures: _ChurnFailures,
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
    except (OSError, subprocess.TimeoutExpired) as exc:
        failures.record("git show", exc)
        return []
    if result.returncode != 0:
        failures.say(f"git show exited {result.returncode}; some commits are not counted")
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
