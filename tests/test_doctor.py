"""Tests for `riskratchet doctor` (P13).

Six checks: paths, baseline, coverage, git, config, suppressions. The
JSON envelope is validated against `schemas/doctor.schema.json` in
test_schemas.py; here we drive the diagnose() function and the CLI
command end-to-end to verify per-check outcomes and remediation text.
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest
from typer.testing import CliRunner

from riskratchet.cli import app
from riskratchet.doctor import CheckStatus, diagnose, summarize

runner = CliRunner()


def _project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("def f(): return 1\n", encoding="utf-8")
    return tmp_path / "src"


def test_diagnose_pass_pass_path() -> None:
    """Smoke: all-pass when everything's set up correctly."""
    # Build a temp-dir via pytest fixture happens at CLI test layer; here
    # we sanity-check the helper independently of the CLI.
    checks = diagnose(
        config_dir=Path("."),
        cfg={"paths": ["src"]},
        paths=[Path("src")] if Path("src").exists() else [Path(".")],
        baseline_file=Path(".riskratchet.json"),
        coverage_path=Path("coverage.json"),
    )
    # We just check the shape — values depend on the cwd state.
    assert len(checks) == 6
    names = [c.name for c in checks]
    assert names == ["paths", "baseline", "coverage", "git", "config", "suppressions"]


def test_doctor_cli_fails_when_baseline_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1, (result.stdout, result.stderr)
    # Status table stays on stdout; remediation routes to stderr.
    assert "baseline" in result.stdout
    assert "FAIL" in result.stdout
    assert "riskratchet baseline" in result.stderr
    assert "riskratchet baseline" not in result.stdout


def test_doctor_cli_passes_when_everything_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    # Create a real baseline so the baseline check passes.
    create = runner.invoke(
        app,
        [
            "baseline",
            str(src),
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert create.exit_code == 0, create.output
    # Initialize a git repo so the git check passes.
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    # Write minimal pyproject so config check passes.
    (tmp_path / "pyproject.toml").write_text(
        dedent(
            """
            [tool.riskratchet]
            paths = ["src"]
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["doctor"])
    # coverage is still warn (no coverage configured); doctor should exit 0
    # because warn is not fail.
    assert result.exit_code == 0, result.output
    assert "FAIL" not in result.stdout


def test_doctor_json_envelope_has_expected_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 1, result.output  # baseline missing
    payload = json.loads(result.stdout)
    assert payload["$schema"].endswith("doctor.schema.json")
    assert isinstance(payload["version"], str)
    assert len(payload["checks"]) == 6
    assert {c["name"] for c in payload["checks"]} == {
        "paths",
        "baseline",
        "coverage",
        "git",
        "config",
        "suppressions",
    }
    summary = payload["summary"]
    assert summary["total"] == 6
    assert summary["passed"] + summary["warned"] + summary["failed"] == 6
    # baseline missing should be among the failures
    failed = [c for c in payload["checks"] if c["status"] == "fail"]
    assert any(c["name"] == "baseline" for c in failed)
    for check in failed:
        assert check["remediation"], "every failing check must carry a remediation"


def test_doctor_warns_when_coverage_is_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    # Write a coverage.json that pre-dates the source file.
    old = tmp_path / "coverage.json"
    old.write_text('{"files": {}}', encoding="utf-8")
    import os
    import time

    # Force coverage mtime older than src.
    old_time = time.time() - 600
    os.utime(old, (old_time, old_time))
    (tmp_path / "pyproject.toml").write_text(
        dedent(
            """
            [tool.riskratchet]
            paths = ["src"]
            coverage = "coverage.json"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    # Force a source-file write so it's clearly newer.
    (src / "m.py").write_text("def f(): return 2\n", encoding="utf-8")
    result = runner.invoke(app, ["doctor", "--json"])
    payload = json.loads(result.stdout)
    cov = next(c for c in payload["checks"] if c["name"] == "coverage")
    assert cov["status"] == "warn"
    assert "stale" in cov["summary"]
    assert "pytest --cov" in (cov["remediation"] or "")


def test_doctor_warns_when_config_unknown_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        dedent(
            """
            [tool.riskratchet]
            paths = ["src"]
            fail_new_abvoe = 40  # typo
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["doctor", "--json"])
    payload = json.loads(result.stdout)
    cfg = next(c for c in payload["checks"] if c["name"] == "config")
    assert cfg["status"] == "warn"
    assert "fail_new_abvoe" in cfg["summary"]


def test_doctor_fail_on_invalid_suppression_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        dedent(
            """
            [tool.riskratchet]
            paths = ["src"]
            allow = ["", "src/legacy/**"]
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["doctor", "--json"])
    payload = json.loads(result.stdout)
    supp = next(c for c in payload["checks"] if c["name"] == "suppressions")
    assert supp["status"] == "fail"


def test_summarize_counts_match_status_distribution() -> None:
    from riskratchet.doctor import DoctorCheck

    checks = [
        DoctorCheck("paths", CheckStatus.PASS, "ok"),
        DoctorCheck("baseline", CheckStatus.FAIL, "missing", remediation="riskratchet baseline"),
        DoctorCheck("coverage", CheckStatus.WARN, "stale"),
        DoctorCheck("git", CheckStatus.PASS, "ok"),
        DoctorCheck("config", CheckStatus.WARN, "unknown key"),
        DoctorCheck("suppressions", CheckStatus.PASS, "0"),
    ]
    s = summarize(checks)
    assert s == {"passed": 3, "warned": 2, "failed": 1, "total": 6}
