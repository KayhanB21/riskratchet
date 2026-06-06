"""Per-revision checkout + coverage regeneration, with a per-SHA cache.

The crux of PR replay: for one commit, check it out in an isolated worktree,
install the repo's deps in a uv venv, run its own test suite under coverage, then
score it with riskratchet and persist a compact ``analyze.json``. Everything is
keyed by full SHA and cached under ``data/calibration/_cache/`` (gitignored), so a
re-run is idempotent — already-replayed revisions short-circuit with no
subprocess. All shelling goes through an injectable ``CommandRunner`` so tests
never clone or spawn anything.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bin.calibration.config import RepoConfig
from bin.calibration.corpus import CACHE_DIR, CORPUS_DIR, analyze_report
from bin.calibration.serial import report_from_dict, report_to_dict
from riskratchet.models import RiskReport

# (argv, cwd, timeout_seconds) -> completed process.
CommandRunner = Callable[[list[str], Path | None, int], "subprocess.CompletedProcess[str]"]

_FAILED_RE = re.compile(r"(\d+) failed")


def _default_runner(argv: list[str], cwd: Path | None, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, timeout=timeout, capture_output=True, text=True, check=False)


@dataclass(frozen=True)
class RevisionResult:
    sha: str
    report: RiskReport | None  # None when the revision could not be coverage-replayed
    coverage_path: Path | None
    pytest_exit_code: int | None
    tests_failed: int | None
    usable_coverage: bool
    cached: bool

    @property
    def ok(self) -> bool:
        return self.report is not None


def revision_cache_dir(repo_name: str, sha: str) -> Path:
    return CACHE_DIR / repo_name / sha[:12]


def _clone_dir(repo_name: str) -> Path:
    return CORPUS_DIR / repo_name


def ensure_clone(repo: RepoConfig, *, run: CommandRunner = _default_runner) -> Path | None:
    """Full (non-shallow) clone so arbitrary base/head SHAs can be checked out.

    Returns the clone path, or None if cloning failed (e.g. offline).
    """
    dest = _clone_dir(repo.name)
    if (dest / ".git").exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = run(["git", "clone", repo.url, str(dest)], None, 1800)
    return dest if proc.returncode == 0 else None


def _prepare_worktree(clone: Path, sha: str, worktree: Path, *, run: CommandRunner) -> bool:
    if worktree.exists():
        return True
    fetch = run(["git", "fetch", "--quiet", "origin", sha], clone, 600)
    if fetch.returncode != 0:
        return False
    worktree.parent.mkdir(parents=True, exist_ok=True)
    add = run(["git", "worktree", "add", "--detach", str(worktree), sha], clone, 300)
    return add.returncode == 0


def _run_suite(
    repo: RepoConfig, worktree: Path, coverage_out: Path, *, run: CommandRunner
) -> tuple[int, int | None]:
    """Install deps + run the suite under coverage. Returns (exit_code, n_failed).

    Does not raise on test failure — coverage.json is still written when some
    tests fail, and partial coverage is usable (with the failure count recorded
    so a reader can discount it).
    """
    extras = f"[{','.join(repo.extras)}]" if repo.extras else ""
    venv = worktree / ".venv"
    run(["uv", "venv", "--python", repo.python, str(venv)], worktree, repo.timeouts.install_seconds)
    install = ["uv", "pip", "install", "--python", str(venv), "-e", f".{extras}"]
    install += ["coverage", "pytest", "pytest-cov"]
    run(install, worktree, repo.timeouts.install_seconds)
    command = repo.test_command.format(coverage_out=str(coverage_out))
    proc = run(["uv", "run", "--python", str(venv), *command.split()], worktree, repo.timeouts.test_seconds)
    failed = _FAILED_RE.search(proc.stdout + proc.stderr)
    return proc.returncode, (int(failed.group(1)) if failed else None)


def _usable(coverage_out: Path) -> bool:
    if not coverage_out.exists():
        return False
    try:
        data = json.loads(coverage_out.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    files = data.get("files") if isinstance(data, dict) else None
    return bool(files)


def replay_revision(
    repo: RepoConfig,
    sha: str,
    *,
    run: CommandRunner = _default_runner,
    force: bool = False,
) -> RevisionResult:
    """Produce (or load from cache) the scored report for one revision."""
    cache = revision_cache_dir(repo.name, sha)
    analyze_path = cache / "analyze.json"
    coverage_path = cache / "coverage.json"
    meta_path = cache / "meta.json"

    if not force and analyze_path.exists():
        return _load_cached(sha, analyze_path, coverage_path, meta_path)

    cache.mkdir(parents=True, exist_ok=True)
    clone = ensure_clone(repo, run=run)
    if clone is None:
        return _unusable(sha, coverage_path)

    worktree = cache / "worktree"
    if not _prepare_worktree(clone, sha, worktree, run=run):
        return _unusable(sha, coverage_path)

    exit_code, failed = _run_suite(repo, worktree, coverage_path, run=run)
    if not _usable(coverage_path):
        _write_meta(meta_path, exit_code, failed, usable=False)
        return RevisionResult(sha, None, None, exit_code, failed, False, cached=False)

    paths = [worktree / p for p in repo.paths] if repo.paths else [worktree]
    report = analyze_report(paths, worktree, coverage_path=coverage_path)
    analyze_path.write_text(json.dumps(report_to_dict(report), indent=2) + "\n", encoding="utf-8")
    _write_meta(meta_path, exit_code, failed, usable=True)
    return RevisionResult(sha, report, coverage_path, exit_code, failed, True, cached=False)


def _load_cached(sha: str, analyze_path: Path, coverage_path: Path, meta_path: Path) -> RevisionResult:
    report = report_from_dict(json.loads(analyze_path.read_text(encoding="utf-8")))
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    return RevisionResult(
        sha=sha,
        report=report,
        coverage_path=coverage_path if coverage_path.exists() else None,
        pytest_exit_code=meta.get("pytest_exit_code"),
        tests_failed=meta.get("tests_failed"),
        usable_coverage=bool(meta.get("usable_coverage", True)),
        cached=True,
    )


def _unusable(sha: str, coverage_path: Path) -> RevisionResult:
    return RevisionResult(sha, None, None, None, None, False, cached=False)


def _write_meta(meta_path: Path, exit_code: int, failed: int | None, *, usable: bool) -> None:
    meta_path.write_text(
        json.dumps(
            {"pytest_exit_code": exit_code, "tests_failed": failed, "usable_coverage": usable},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
