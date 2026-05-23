"""End-to-end integration tests.

These exercise the full pipeline a downstream user would actually run:

- `pytest --cov --cov-report=json:coverage.json` to produce a real
  `coverage.json` (i.e. not synthesized in-test).
- `riskratchet baseline` against that coverage to snapshot the project.
- `riskratchet check` to verify the clean state and to verify a real
  regression is caught.
- A pre-commit invocation wired through `repos: local` to confirm the
  CLI shells out cleanly under the hook driver.

Each test builds a fresh fixture project in `tmp_path` so the assertions
are hermetic and don't depend on the parent repo's state.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

RISKRATCHET_BIN = shutil.which("riskratchet")
PRECOMMIT_BIN = shutil.which("pre-commit")


def _write_project(root: Path) -> None:
    """Lay down a small src-layout project with one tested + one untested function."""
    pkg = root / "src" / "sample"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text(
        dedent(
            """
            def add(a: int, b: int) -> int:
                return a + b


            def risky(x: int) -> int:
                if x > 100:
                    return x * 2
                if x > 50:
                    return x + 10
                if x > 0:
                    return x
                return -1
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_core.py").write_text(
        dedent(
            """
            from sample.core import add


            def test_add() -> None:
                assert add(2, 3) == 5
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        dedent(
            """
            [project]
            name = "sample"
            version = "0.0.1"
            requires-python = ">=3.10"

            [tool.pytest.ini_options]
            testpaths = ["tests"]
            pythonpath = ["src"]

            [tool.coverage.run]
            source = ["src/sample"]
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def _bin(path: str | None, name: str) -> str:
    if path is None:
        pytest.skip(f"{name} not on PATH")
    return path


@pytest.mark.skipif(RISKRATCHET_BIN is None, reason="riskratchet not on PATH")
def test_full_pipeline_clean_check(tmp_path: Path) -> None:
    """pytest --cov, then baseline, then check returns 0 when nothing changed."""
    rr = _bin(RISKRATCHET_BIN, "riskratchet")
    _write_project(tmp_path)

    pytest_result = _run(
        [sys.executable, "-m", "pytest", "--cov", "--cov-report=json:coverage.json", "-q"],
        cwd=tmp_path,
    )
    assert pytest_result.returncode == 0, pytest_result.stdout + pytest_result.stderr
    coverage_json = tmp_path / "coverage.json"
    assert coverage_json.exists()
    payload = json.loads(coverage_json.read_text(encoding="utf-8"))
    assert "files" in payload

    baseline_result = _run(
        [rr, "baseline", "src", "--coverage", "coverage.json", "--output", ".riskratchet.json", "--no-git"],
        cwd=tmp_path,
    )
    assert baseline_result.returncode == 0, baseline_result.stdout + baseline_result.stderr
    baseline = json.loads((tmp_path / ".riskratchet.json").read_text(encoding="utf-8"))
    assert {e["qualname"] for e in baseline["entries"]} == {"add", "risky"}

    check_result = _run(
        [rr, "check", "src", "--coverage", "coverage.json", "--baseline", ".riskratchet.json", "--no-git"],
        cwd=tmp_path,
    )
    assert check_result.returncode == 0, check_result.stdout + check_result.stderr


@pytest.mark.skipif(RISKRATCHET_BIN is None, reason="riskratchet not on PATH")
def test_full_pipeline_regression_is_caught(tmp_path: Path) -> None:
    """After degrading the source, `check` exits 1 with regression output."""
    rr = _bin(RISKRATCHET_BIN, "riskratchet")
    _write_project(tmp_path)

    _run(
        [sys.executable, "-m", "pytest", "--cov", "--cov-report=json:coverage.json", "-q"],
        cwd=tmp_path,
    ).check_returncode()
    _run(
        [rr, "baseline", "src", "--coverage", "coverage.json", "--output", ".riskratchet.json", "--no-git"],
        cwd=tmp_path,
    ).check_returncode()

    # Replace `risky` with a 10-branch monster so its score crosses
    # fail_new_above (default 50) and dwarfs the original score.
    core = tmp_path / "src" / "sample" / "core.py"
    core.write_text(
        dedent(
            """
            def add(a: int, b: int) -> int:
                return a + b


            def risky(a, b, c, d, e, f, g, h, i, j):
                if a:
                    return 1
                if b:
                    return 2
                if c:
                    return 3
                if d:
                    return 4
                if e:
                    return 5
                if f:
                    return 6
                if g:
                    return 7
                if h:
                    return 8
                if i:
                    return 9
                if j:
                    return 10
                return 0
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    _run(
        [sys.executable, "-m", "pytest", "--cov", "--cov-report=json:coverage.json", "-q"],
        cwd=tmp_path,
    ).check_returncode()

    check_result = _run(
        [rr, "check", "src", "--coverage", "coverage.json", "--baseline", ".riskratchet.json", "--no-git"],
        cwd=tmp_path,
    )
    assert check_result.returncode == 1, (
        f"expected exit 1; got {check_result.returncode}\n"
        f"stdout:\n{check_result.stdout}\nstderr:\n{check_result.stderr}"
    )
    assert "risky" in check_result.stdout


@pytest.mark.skipif(RISKRATCHET_BIN is None, reason="riskratchet not on PATH")
def test_full_pipeline_missing_baseline_exits_two(tmp_path: Path) -> None:
    """`check` with no baseline file exits 2 (usage error), not 1."""
    rr = _bin(RISKRATCHET_BIN, "riskratchet")
    _write_project(tmp_path)
    _run(
        [sys.executable, "-m", "pytest", "--cov", "--cov-report=json:coverage.json", "-q"],
        cwd=tmp_path,
    ).check_returncode()

    check_result = _run(
        [rr, "check", "src", "--coverage", "coverage.json", "--baseline", "nope.json", "--no-git"],
        cwd=tmp_path,
    )
    assert check_result.returncode == 2
    assert "baseline file not found" in check_result.stderr.lower()


@pytest.mark.skipif(RISKRATCHET_BIN is None, reason="riskratchet not on PATH")
@pytest.mark.skipif(PRECOMMIT_BIN is None, reason="pre-commit not on PATH")
def test_pre_commit_invokes_riskratchet_cleanly(tmp_path: Path) -> None:
    """A `repo: local` pre-commit hook calling riskratchet runs to a clean pass."""
    rr = _bin(RISKRATCHET_BIN, "riskratchet")
    pc = _bin(PRECOMMIT_BIN, "pre-commit")
    _write_project(tmp_path)

    _run(
        [sys.executable, "-m", "pytest", "--cov", "--cov-report=json:coverage.json", "-q"],
        cwd=tmp_path,
    ).check_returncode()
    _run(
        [rr, "baseline", "src", "--coverage", "coverage.json", "--output", ".riskratchet.json", "--no-git"],
        cwd=tmp_path,
    ).check_returncode()

    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Tester"],
        ["git", "config", "commit.gpgsign", "false"],
    ):
        subprocess.run(cmd, cwd=tmp_path, check=True)

    (tmp_path / ".pre-commit-config.yaml").write_text(
        dedent(
            f"""
            repos:
              - repo: local
                hooks:
                  - id: riskratchet
                    name: riskratchet check
                    entry: {rr} check src --coverage coverage.json --baseline .riskratchet.json --no-git
                    language: system
                    pass_filenames: false
                    always_run: true
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    # Track only the source + config; leave coverage.json + .riskratchet.json
    # untracked so pre-commit's --all-files pass doesn't have to roll back over them.
    subprocess.run(
        ["git", "add", "src", "tests", "pyproject.toml", ".pre-commit-config.yaml"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    result = _run([pc, "run", "--all-files"], cwd=tmp_path)
    assert result.returncode == 0, f"pre-commit failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "riskratchet" in result.stdout.lower()


@pytest.mark.skipif(RISKRATCHET_BIN is None, reason="riskratchet not on PATH")
@pytest.mark.skipif(PRECOMMIT_BIN is None, reason="pre-commit not on PATH")
def test_pre_commit_fails_when_riskratchet_finds_regression(tmp_path: Path) -> None:
    """Pre-commit exits non-zero when the underlying riskratchet check fails."""
    rr = _bin(RISKRATCHET_BIN, "riskratchet")
    pc = _bin(PRECOMMIT_BIN, "pre-commit")
    _write_project(tmp_path)

    # Hand-write a baseline that scores every function very low so the actual
    # uncovered `risky` function trips the threshold.
    (tmp_path / ".riskratchet.json").write_text(
        json.dumps({"version": "1", "entries": []}),
        encoding="utf-8",
    )
    _run(
        [sys.executable, "-m", "pytest", "--cov", "--cov-report=json:coverage.json", "-q"],
        cwd=tmp_path,
    ).check_returncode()

    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "test@example.com"],
        ["git", "config", "user.name", "Tester"],
        ["git", "config", "commit.gpgsign", "false"],
    ):
        subprocess.run(cmd, cwd=tmp_path, check=True)

    (tmp_path / ".pre-commit-config.yaml").write_text(
        dedent(
            f"""
            repos:
              - repo: local
                hooks:
                  - id: riskratchet
                    name: riskratchet check
                    entry: {rr} check src --coverage coverage.json --baseline .riskratchet.json --no-git --fail-new-above 10
                    language: system
                    pass_filenames: false
                    always_run: true
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    subprocess.run(
        ["git", "add", "src", "tests", "pyproject.toml", ".pre-commit-config.yaml"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    result = _run([pc, "run", "--all-files"], cwd=tmp_path)
    assert result.returncode != 0, "pre-commit should fail when riskratchet check fails"
