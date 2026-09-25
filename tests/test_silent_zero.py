"""A zero that is really a failure must say so (0.3.7).

Churn scores 0 in three quite different situations, and until now they were indistinguishable:
the code genuinely has not changed; riskratchet is anchored somewhere with no history to read;
or git failed outright. Only the first is a measurement. The other two wrote themselves into the
next baseline as though they were, and `doctor` actively vouched for the second.

**Nothing here moves a score.** Churn in 0.3.7 is still collected relative to the configuration
directory, deliberately — attributing it from the repository root would raise the churn component
on nearly every function in a monorepo package at once, and a patch release must not turn a green
gate red. 0.4.0 makes that change. This release only makes the tool say what it is doing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import TYPE_CHECKING, Any

import pytest
from typer.testing import CliRunner

from git_fixtures import commit, git, init_repo, make_nested_config_repo
from riskratchet.cli import app
from riskratchet.doctor import CheckStatus, diagnose
from riskratchet.git import (
    churn_root_mismatch,
    collect_file_churn,
    collect_function_churn,
    head_sha,
    repo_info,
)
from riskratchet.models import FunctionId, FunctionSpan

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_inherited_git_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop any `GIT_*` pointing at an outer repository.

    The repo's own pre-commit hook runs this suite from inside a `git commit`, where `GIT_DIR`
    and `GIT_INDEX_FILE` are set; riskratchet shells out to git and would read that repository
    instead of the fixture's.
    """
    for key in [name for name in os.environ if name.startswith("GIT_")]:
        monkeypatch.delenv(key, raising=False)


# --------------------------------------------------------------------------- the nested root


def test_the_repository_root_is_found_from_a_nested_config_dir(tmp_path: Path) -> None:
    """`.git` lives at the top level, so probing for it below reported "no repository" for a
    tree that is very much in one."""
    root, config_dir = make_nested_config_repo(tmp_path)
    assert not (config_dir / ".git").exists()
    info = repo_info(config_dir)
    assert info is not None and info.root.resolve() == root.resolve()
    assert churn_root_mismatch(config_dir) is not None
    assert churn_root_mismatch(root) is None


def test_the_redaction_salt_survives_a_nested_config_dir(tmp_path: Path) -> None:
    """`head_sha` gated on `root/.git`, so a monorepo package fell back to *unsalted*
    redaction — the one outcome salting exists to prevent — while sitting in a repository
    with a perfectly good HEAD."""
    root, config_dir = make_nested_config_repo(tmp_path)
    assert head_sha(config_dir) is not None
    assert head_sha(config_dir) == head_sha(root)
    assert head_sha(tmp_path.parent / "definitely-not-a-repo") is None


def test_check_says_churn_is_dead_in_a_nested_config_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, config_dir = make_nested_config_repo(tmp_path)
    monkeypatch.chdir(config_dir)
    result = runner.invoke(app, ["scan", "src", "--no-auto-cov"])
    assert result.exit_code == 0, result.stderr
    assert "churn is scoring 0 for every function" in result.stderr
    assert "0.4.0 will score churn from the repository root" in result.stderr


def test_saying_so_does_not_change_a_single_score(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The release-defining constraint. The warning is new; the numbers are not."""
    _, config_dir = make_nested_config_repo(tmp_path)
    monkeypatch.chdir(config_dir)
    result = runner.invoke(app, ["scan", "src", "--no-auto-cov", "--format", "json"])
    payload = json.loads(result.stdout)
    assert payload["functions"], "fixture produced no functions"
    assert all(fn["components"]["churn"] == 0 for fn in payload["functions"])


def test_no_warning_when_the_config_dir_is_the_repository_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("def f(): return 1\n", encoding="utf-8")
    commit(tmp_path, "src/m.py", "def f(): return 1\n")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["scan", "src", "--no-auto-cov"])
    assert "churn is scoring 0" not in result.stderr


# --------------------------------------------------------------------------- doctor agreement


def test_doctor_no_longer_vouches_for_a_tree_where_churn_is_dead(tmp_path: Path) -> None:
    """`doctor` shelled `git rev-parse --git-dir`, which succeeds from *any* subdirectory, so
    it reported PASS "git repo" on precisely the layout where every function's churn is 0."""
    _, config_dir = make_nested_config_repo(tmp_path)
    checks = {
        c.name: c
        for c in diagnose(
            config_dir=config_dir,
            cfg={"paths": ["src"]},
            paths=[config_dir / "src"],
            baseline_file=config_dir / ".riskratchet.json",
            coverage_path=None,
        )
    }
    assert checks["git"].status is CheckStatus.WARN
    assert "repository root is" in checks["git"].summary


def test_doctor_and_check_agree_two_levels_below_the_git_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two levels, not one: a fix that resolves only the immediate parent would pass a
    one-level fixture and still be wrong. The two commands must reach the same verdict about
    whether churn can be collected, because disagreeing is how this went unnoticed."""
    _, config_dir = make_nested_config_repo(tmp_path, nested="services/api")
    assert config_dir == tmp_path / "services" / "api"
    monkeypatch.chdir(config_dir)

    doctor = runner.invoke(app, ["doctor", "--json"])
    row = next(c for c in json.loads(doctor.stdout)["checks"] if c["name"] == "git")
    scan = runner.invoke(app, ["scan", "src", "--no-auto-cov"])

    doctor_says_dead = row["status"] == "warn" and "repository root is" in row["summary"]
    check_says_dead = "churn is scoring 0 for every function" in scan.stderr
    assert doctor_says_dead is check_says_dead is True


def test_doctor_still_passes_git_at_the_repository_root(tmp_path: Path) -> None:
    init_repo(tmp_path)
    commit(tmp_path, "src/m.py", "def f(): return 1\n")
    checks = {
        c.name: c
        for c in diagnose(
            config_dir=tmp_path,
            cfg={"paths": ["src"]},
            paths=[tmp_path / "src"],
            baseline_file=tmp_path / ".riskratchet.json",
            coverage_path=None,
        )
    }
    assert checks["git"].status is CheckStatus.PASS


# --------------------------------------------------------------------------- git failures


class _Exploder:
    """Make `git` fail the way a resource-starved container does, for one command prefix."""

    def __init__(self, exc: BaseException, only: str | None = None) -> None:
        self._real = subprocess.run
        self._exc = exc
        self._only = only

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        argv = args[0] if args else kwargs.get("args")
        if isinstance(argv, (list, tuple)) and argv and argv[0] == "git":
            words = [str(part) for part in argv]
            if self._only is None or self._only in words:
                raise self._exc
        return self._real(*args, **kwargs)


@pytest.mark.parametrize(
    "exc",
    [
        OSError(12, "Cannot allocate memory"),
        subprocess.TimeoutExpired(cmd="git", timeout=30),
        PermissionError(13, "Permission denied"),
    ],
    ids=["enomem", "timeout", "eacces"],
)
def test_a_git_failure_warns_instead_of_returning_a_silent_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exc: BaseException
) -> None:
    """Only `FileNotFoundError` and `TimeoutExpired` were caught, so a plain `OSError` from
    fork/exec escaped and Typer turned it into exit 1 — "a gate tripped" — for an I/O failure.
    AGENTS.md: never let an I/O failure exit 1. And a caught failure must not go silent either:
    churn 0 that is really an error reads as "nothing changed" and gets baselined as fact.
    """
    init_repo(tmp_path)
    commit(tmp_path, "a.py", "x = 1\n")
    said: list[str] = []
    monkeypatch.setattr(subprocess, "run", _Exploder(exc, only="log"))

    result = collect_function_churn(
        tmp_path,
        [(FunctionId("a.py", "f"), FunctionSpan(1, 2))],
        on_error=said.append,
    )

    assert result == {}
    assert said, "the failure was swallowed silently"
    assert "churn is scoring 0" in said[0]


def test_a_git_failure_is_reported_once_not_once_per_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_changed_ranges_for_commit` runs per commit, so a repository-wide failure would print
    one line per commit in the window."""
    init_repo(tmp_path)
    for value in range(4):
        commit(tmp_path, "a.py", f"x = {value}\n")
    said: list[str] = []
    monkeypatch.setattr(subprocess, "run", _Exploder(OSError(12, "Cannot allocate memory"), only="show"))

    collect_function_churn(tmp_path, [(FunctionId("a.py", "f"), FunctionSpan(1, 2))], on_error=said.append)

    assert len(said) == 1, said


def test_head_sha_and_file_churn_survive_an_oserror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both degrade to "no answer" by contract; the point is that neither raises."""
    init_repo(tmp_path)
    commit(tmp_path, "a.py", "x = 1\n")
    monkeypatch.setattr(subprocess, "run", _Exploder(OSError(12, "Cannot allocate memory")))
    assert head_sha(tmp_path) is None
    assert collect_file_churn(tmp_path) == {}
    assert repo_info(tmp_path) is None


def test_a_repository_with_no_commits_stays_silent(tmp_path: Path) -> None:
    """`git log` exits 128 on a fresh `git init` with "does not have any commits yet". There
    is genuinely no churn to find, and warning would fire on every run in a new repo — the
    kind of noise that teaches people to ignore the warning that matters."""
    init_repo(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    said: list[str] = []
    assert (
        collect_function_churn(
            tmp_path, [(FunctionId("a.py", "f"), FunctionSpan(1, 2))], on_error=said.append
        )
        == {}
    )
    assert said == []


def test_the_cli_reports_a_churn_failure_without_failing_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The per-door convention: churn failing is not a gate verdict."""
    init_repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("def f(x):\n    if x: return 1\n    return 0\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "one")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(subprocess, "run", _Exploder(OSError(12, "Cannot allocate memory"), only="log"))

    result = runner.invoke(app, ["scan", "src", "--no-auto-cov"])

    assert result.exit_code == 0, result.stderr
    assert "churn is scoring 0" in result.stderr


# --------------------------------------------------------------------------- doctor's own walk


def test_doctor_does_not_blame_a_vendored_file_for_stale_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The staleness walk was a bare rglob with no hidden-parent filter, so on the default
    `paths = ["."]` it descended into `.venv` and reported "coverage older than
    .venv/.../_version.py (stale)" with the remediation "re-run pytest" — which cannot help,
    because that file is not in the coverage report and never will be."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("def f(): return 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.riskratchet]\npaths = ["."]\ncoverage = "coverage.json"\n', encoding="utf-8"
    )
    cov = tmp_path / "coverage.json"
    cov.write_text(
        json.dumps(
            {
                "meta": {"version": "7.0"},
                "files": {
                    "src/m.py": {
                        "executed_lines": [1],
                        "missing_lines": [],
                        "summary": {"covered_lines": 1, "num_statements": 1, "percent_covered": 100.0},
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    vendored = tmp_path / ".venv" / "lib" / "site-packages" / "dep"
    vendored.mkdir(parents=True)
    stale_marker = vendored / "_version.py"
    stale_marker.write_text('__version__ = "1"\n', encoding="utf-8")
    base = cov.stat().st_mtime
    os.utime(stale_marker, (base + 100, base + 100))
    os.utime(tmp_path / "src" / "m.py", (base - 100, base - 100))

    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor", "--json"])
    row = next(c for c in json.loads(result.stdout)["checks"] if c["name"] == "coverage")

    assert ".venv" not in row["summary"]
    assert row["status"] == "pass", row


def test_a_dangling_symlink_does_not_take_doctor_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`candidate.stat()` was unguarded, so a broken symlink raised `FileNotFoundError`
    straight out of `doctor` — exit 1 on an I/O failure."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("def f(): return 1\n", encoding="utf-8")
    (tmp_path / "src" / "broken.py").symlink_to(tmp_path / "does-not-exist.py")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.riskratchet]\npaths = ["src"]\ncoverage = "coverage.json"\n', encoding="utf-8"
    )
    (tmp_path / "coverage.json").write_text('{"meta": {"version": "7.0"}, "files": {}}', encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor"])

    assert not isinstance(result.exception, FileNotFoundError), result.exception
    assert "Traceback" not in result.stdout


# --------------------------------------------------------------------------- the third door


def test_the_plugin_says_churn_is_dead_too(tmp_path: Path) -> None:
    """All three doors, same finding. The plugin has no exit 2 available, so it is a message."""
    _, config_dir = make_nested_config_repo(tmp_path)
    (config_dir / "pyproject.toml").write_text(
        '[tool.riskratchet]\npaths = ["src"]\nallow_missing_coverage = true\n', encoding="utf-8"
    )
    (config_dir / "test_smoke.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    baseline = config_dir / ".riskratchet.json"
    written = runner.invoke(
        app,
        [
            "baseline",
            str(config_dir / "src"),
            "--output",
            str(baseline),
            "--allow-missing-coverage",
            "--no-auto-cov",
        ],
    )
    assert written.exit_code == 0, (written.stdout, written.stderr)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "riskratchet",
            "--riskratchet",
            "-q",
            "--cov=src",
            "--cov-report=json:coverage.json",
            "test_smoke.py",
        ],
        cwd=config_dir,
        capture_output=True,
        text=True,
        timeout=120,
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    )

    assert "churn is scoring 0 for every function" in completed.stdout, completed.stdout


def test_doctor_tells_missing_git_apart_from_a_missing_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "install git" and "git init" are not interchangeable advice, so the two stay separate
    rows even though `repo_info` collapses both to `None`."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("def f(): return 1\n", encoding="utf-8")
    kwargs = {
        "config_dir": tmp_path,
        "cfg": {"paths": ["src"]},
        "paths": [tmp_path / "src"],
        "baseline_file": tmp_path / ".riskratchet.json",
        "coverage_path": None,
    }

    # git absent entirely
    monkeypatch.setattr(subprocess, "run", _Exploder(FileNotFoundError(2, "git")))
    absent = {c.name: c for c in diagnose(**kwargs)}["git"]  # type: ignore[arg-type]
    assert absent.status is CheckStatus.WARN
    assert "not on PATH" in absent.summary
    assert "install git" in (absent.remediation or "")

    # git present, but this is not a repository
    monkeypatch.undo()
    not_a_repo = {c.name: c for c in diagnose(**kwargs)}["git"]  # type: ignore[arg-type]
    assert not_a_repo.status is CheckStatus.WARN
    assert "not a git repo" in not_a_repo.summary
    assert "git init" in (not_a_repo.remediation or "")


def test_doctor_survives_a_git_that_times_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A hung git is an I/O failure, not a verdict: `doctor` reports it and keeps going."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("def f(): return 1\n", encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", _Exploder(subprocess.TimeoutExpired(cmd="git", timeout=5)))
    row = {
        c.name: c
        for c in diagnose(
            config_dir=tmp_path,
            cfg={"paths": ["src"]},
            paths=[tmp_path / "src"],
            baseline_file=tmp_path / ".riskratchet.json",
            coverage_path=None,
        )
    }["git"]
    assert row.status is CheckStatus.WARN
    assert "not on PATH" in row.summary
