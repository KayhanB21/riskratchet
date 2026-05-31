"""End-to-end first-5-minutes integration test.

Exercises the canonical adoption flow as a single CliRunner sequence:

    init  → doctor  → baseline  → check

so a future regression in any one of these commands surfaces here even
if the per-command unit tests still pass. Coverage data is hand-written
(no real `pytest --cov` subprocess) so the test stays hermetic.
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest
from typer.testing import CliRunner

from riskratchet.cli import app

runner = CliRunner()


def _write_minimal_project(tmp_path: Path) -> None:
    """One source file + a minimal coverage.json that maps it."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text(
        dedent(
            """
            def trivial():
                return 1

            def branchy(x):
                if x > 0:
                    return 1
                return 0
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    coverage = {
        "meta": {"branch_coverage": True},
        "files": {
            "src/m.py": {
                "summary": {"num_statements": 5, "missing_lines": 0},
                "executed_lines": [1, 2, 4, 5, 6, 7],
                "missing_lines": [],
                "executed_branches": [[5, 6], [5, 7]],
                "missing_branches": [],
            }
        },
    }
    (tmp_path / "coverage.json").write_text(json.dumps(coverage, indent=2), encoding="utf-8")


def test_first_five_minutes_init_doctor_baseline_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_minimal_project(tmp_path)

    init_result = runner.invoke(app, ["init", "--no-baseline"])
    assert init_result.exit_code == 0, (init_result.stdout, init_result.stderr)
    pyproject = tmp_path / "pyproject.toml"
    assert pyproject.exists()
    assert "[tool.riskratchet]" in pyproject.read_text(encoding="utf-8")

    doctor_result = runner.invoke(app, ["doctor"])
    # Baseline still missing at this point → exit 1.
    assert doctor_result.exit_code == 1, (doctor_result.stdout, doctor_result.stderr)
    assert "baseline" in doctor_result.stdout
    assert "FAIL" in doctor_result.stdout
    # Per the P13 stderr-routing rule, the remediation belongs on stderr.
    assert "riskratchet baseline" in doctor_result.stderr

    baseline_result = runner.invoke(
        app,
        ["baseline", "src", "--coverage", "coverage.json", "--no-auto-cov", "--no-git"],
    )
    assert baseline_result.exit_code == 0, (baseline_result.stdout, baseline_result.stderr)
    assert (tmp_path / ".riskratchet.json").exists()

    doctor_after = runner.invoke(app, ["doctor"])
    assert doctor_after.exit_code == 0, (doctor_after.stdout, doctor_after.stderr)
    assert "FAIL" not in doctor_after.stdout

    check_result = runner.invoke(
        app,
        [
            "check",
            "src",
            "--baseline",
            ".riskratchet.json",
            "--coverage",
            "coverage.json",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert check_result.exit_code == 0, (check_result.stdout, check_result.stderr)
