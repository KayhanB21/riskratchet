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
