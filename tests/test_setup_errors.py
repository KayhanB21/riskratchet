"""Tests for P26: actionable setup errors.

Each test exercises one of the top first-failure sites and asserts that the
remediation command appears in stderr. The shape we contract on:

  riskratchet: <headline>

  Fix one of:
    1. <description>
         <command>

so the existence of a concrete copy-pasteable command is the load-bearing
invariant — not the exact wording of the headline.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from typer.testing import CliRunner

from riskratchet.cli import app

runner = CliRunner()


def _project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text(
        dedent(
            """
            def trivial():
                return 1
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return tmp_path / "src"


def test_missing_coverage_emits_pytest_remediation_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    # No coverage anywhere, no --allow-missing-coverage: hard fail.
    result = runner.invoke(app, ["baseline", str(src), "--no-auto-cov", "--no-git"])
    assert result.exit_code == 2, result.output
    assert "Fix one of:" in result.stderr
    assert "pytest --cov" in result.stderr
    assert "--allow-missing-coverage" in result.stderr
    assert "--no-auto-cov" in result.stderr


def test_missing_baseline_emits_baseline_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    result = runner.invoke(
        app,
        ["check", str(src), "--allow-missing-coverage", "--no-auto-cov", "--no-git"],
    )
    assert result.exit_code == 2, result.output
    assert "baseline file not found" in result.stderr
    assert "Fix one of:" in result.stderr
    assert "riskratchet baseline" in result.stderr
    # P28 fallback is mentioned as a remediation:
    assert "--fail-above" in result.stderr


def test_missing_baseline_in_diff_emits_baseline_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    result = runner.invoke(
        app,
        ["diff", str(src), "--allow-missing-coverage", "--no-auto-cov", "--no-git"],
    )
    assert result.exit_code == 2, result.output
    assert "baseline file not found" in result.stderr
    assert "riskratchet baseline" in result.stderr


def test_malformed_baseline_emits_regenerate_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    # Junk-bytes baseline so json.loads raises and triggers the new helper.
    baseline = tmp_path / "bad.json"
    baseline.write_text("{not valid json", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "check",
            str(src),
            "--baseline",
            str(baseline),
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 2, result.output
    assert "cannot read baseline" in result.stderr
    assert "Fix one of:" in result.stderr
    assert "riskratchet baseline" in result.stderr


def test_missing_scan_path_arg_emits_remediation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    # Path that does not exist on disk — today's behaviour is a silent empty
    # report; P26 fails fast with an actionable error.
    result = runner.invoke(
        app,
        ["scan", "src/typo.py", "--no-auto-cov", "--no-git"],
    )
    assert result.exit_code == 2, result.output
    assert "src/typo.py" in result.stderr
    assert "Fix one of:" in result.stderr
    # Remediation hint is "check spelling, list a different path":
    assert "Check the path spelling" in result.stderr


def test_missing_scan_path_in_config_emits_remediation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    # No src/ dir; config points at non-existent path.
    (tmp_path / "pyproject.toml").write_text(
        dedent(
            """
            [tool.riskratchet]
            paths = ["nonexistent_pkg"]
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["scan", "--no-auto-cov", "--no-git"],
    )
    assert result.exit_code == 2, result.output
    assert "nonexistent_pkg" in result.stderr
    assert "Edit pyproject.toml" in result.stderr


def test_stale_coverage_test_command_failure_emits_remediation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: `riskratchet baseline` with auto-coverage enabled, but the
    test command produces no coverage.json, surfaces the new remediation
    block on stderr. Overrides the autouse `refuse` stub for this one
    test so the auto-coverage path actually runs.
    """
    import riskratchet.auto_coverage as auto_coverage

    def fake_runner_that_writes_nothing(command: str, cwd: Path) -> int:
        # Match the real runner shape (str, Path) but skip the side effect
        # — the test asserts that an empty result triggers the hint.
        return 0

    monkeypatch.setattr(auto_coverage, "_default_runner", fake_runner_that_writes_nothing)
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    result = runner.invoke(
        app,
        ["baseline", str(src), "--no-git"],
    )
    # exit 2 because auto-coverage produced nothing and no fallback is allowed.
    assert result.exit_code == 2, (result.stdout, result.stderr)
    assert "test command did not produce" in result.stderr
    assert "Fix one of:" in result.stderr
    assert "pytest --cov" in result.stderr
    assert "--no-auto-cov" in result.stderr


def _seed_baseline(src: Path) -> None:
    """Write a real baseline so `check`/`diff` get past their baseline gate.

    Both commands resolve the baseline before touching coverage, so without
    this they exit 2 on "baseline file not found" and never reach the coverage
    boundary under test.
    """
    result = runner.invoke(
        app, ["baseline", str(src), "--allow-missing-coverage", "--no-auto-cov", "--no-git"]
    )
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("command", ["scan", "check", "diff", "baseline"])
def test_malformed_coverage_emits_setup_error(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A coverage file that won't parse is a setup problem, not a crash.

    Before 0.3.2 every command wrapped `build_report` in `except ImportError`
    only, so `load_coverage`'s `ValueError` escaped as a raw traceback. All
    four commands now share one boundary (`cli._build_report_or_exit`).
    """
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    _seed_baseline(src)
    bad = tmp_path / "coverage.json"
    bad.write_text("not json at all", encoding="utf-8")

    result = runner.invoke(app, [command, str(src), "--coverage", str(bad), "--no-auto-cov", "--no-git"])

    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.output
    assert "could not read coverage file" in result.stderr
    assert "Fix one of:" in result.stderr
    assert "pytest --cov" in result.stderr


@pytest.mark.parametrize("command", ["scan", "check", "diff", "baseline"])
def test_coverage_file_without_files_section_emits_setup_error(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valid JSON, wrong shape — the other `ValueError` branch in `load_coverage`."""
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    _seed_baseline(src)
    bad = tmp_path / "coverage.json"
    bad.write_text('{"meta": {}}', encoding="utf-8")

    result = runner.invoke(app, [command, str(src), "--coverage", str(bad), "--no-auto-cov", "--no-git"])

    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.output
    assert "has no `files` section" in result.stderr
    assert "Fix one of:" in result.stderr


def test_scan_with_missing_coverage_map_shard_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`scan` passes `allow_missing=True`, so an absent shard must warn, not crash."""
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)

    result = runner.invoke(
        app,
        ["scan", str(src), "--coverage-map", f"src={tmp_path / 'absent.json'}", "--no-auto-cov", "--no-git"],
    )

    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output
    assert "treating that prefix as no coverage" in result.stderr
    assert "pytest --cov" in result.stderr


def test_scan_with_malformed_coverage_map_shard_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    junk = tmp_path / "junk.json"
    junk.write_text("not json", encoding="utf-8")

    result = runner.invoke(
        app,
        ["scan", str(src), "--coverage-map", f"src={junk}", "--no-auto-cov", "--no-git"],
    )

    assert result.exit_code == 0, result.output
    assert "coverage-map shard unusable" in result.stderr
    assert "could not read" in result.stderr


def test_strict_missing_coverage_map_shard_still_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`check` does not allow missing coverage, so the strict path is unchanged."""
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    _seed_baseline(src)

    result = runner.invoke(
        app,
        ["check", str(src), "--coverage-map", f"src={tmp_path / 'absent.json'}", "--no-auto-cov", "--no-git"],
    )

    assert result.exit_code == 2, result.output
    assert "coverage-map[src] file not found" in result.stderr
    assert "--allow-missing-coverage" in result.stderr


def test_shallow_clone_emits_churn_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A shallow clone silently zeroes churn, so say so.

    This is the runtime half of the `fetch-depth: 0` fix: an adopter whose CI
    still uses a depth-1 checkout gets told why their scores disagree with the
    baseline instead of silently getting different numbers.
    """
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "shallow").write_text("deadbeef\n", encoding="utf-8")

    result = runner.invoke(app, ["scan", str(src), "--no-auto-cov"])

    assert result.exit_code == 0, result.output
    assert "shallow clone detected" in result.stderr
    assert "fetch-depth: 0" in result.stderr


def test_no_git_suppresses_shallow_clone_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "shallow").write_text("deadbeef\n", encoding="utf-8")

    result = runner.invoke(app, ["scan", str(src), "--no-auto-cov", "--no-git"])

    assert result.exit_code == 0, result.output
    assert "shallow clone detected" not in result.stderr
