"""Tests for the P25 zero-flag `scan` next-step footer.

The footer appears on stdout, only when:
  - format is `table` (the default),
  - `--quiet`, `--summary`, and `--output` are all unset, and
  - no baseline file exists at the resolved baseline path.

Wording adapts to whether any functions cross severity=medium.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from typer.testing import CliRunner

from riskratchet.cli import app

runner = CliRunner()


def _high_risk_project(tmp_path: Path) -> Path:
    """A file with one highly-branchy function so severity >= medium."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text(
        dedent(
            """
            def gnarly(x):
                if x > 0:
                    if x > 1:
                        if x > 2:
                            if x > 3:
                                if x > 4:
                                    return 5
                                return 4
                            return 3
                        return 2
                    return 1
                return 0
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return tmp_path / "src"


def _low_risk_project(tmp_path: Path) -> Path:
    """A file with only trivial functions so nothing crosses medium severity."""
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


def test_footer_shows_lock_in_message_when_risky_and_no_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    src = _high_risk_project(tmp_path)
    result = runner.invoke(app, ["scan", str(src), "--no-auto-cov", "--no-git"])
    assert result.exit_code == 0, result.output
    assert "lock in this state" in result.stdout
    assert "riskratchet baseline" in result.stdout
    assert "--fail-above" in result.stdout


def test_footer_shows_nothing_to_baseline_when_clean_and_no_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty-state: --min-score above the only function's score yields a
    filtered report with no risky candidates; footer reports the empty state."""
    monkeypatch.chdir(tmp_path)
    src = _low_risk_project(tmp_path)
    result = runner.invoke(
        app,
        [
            "scan",
            str(src),
            "--min-score",
            "100",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "nothing to baseline yet" in result.stdout


def test_footer_suppressed_when_baseline_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _high_risk_project(tmp_path)
    baseline = tmp_path / ".riskratchet.json"
    # First, create a baseline so the steady-state path applies.
    create = runner.invoke(
        app,
        [
            "baseline",
            str(src),
            "--output",
            str(baseline),
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert create.exit_code == 0, create.output
    result = runner.invoke(app, ["scan", str(src), "--no-auto-cov", "--no-git"])
    assert result.exit_code == 0, result.output
    assert "lock in this state" not in result.stdout
    assert "nothing to baseline" not in result.stdout


def test_footer_suppressed_for_json_format(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _high_risk_project(tmp_path)
    result = runner.invoke(app, ["scan", str(src), "--json", "--no-auto-cov", "--no-git"])
    assert result.exit_code == 0, result.output
    assert "riskratchet baseline" not in result.stdout


def test_footer_suppressed_for_summary_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _high_risk_project(tmp_path)
    result = runner.invoke(app, ["scan", str(src), "--summary", "--no-auto-cov", "--no-git"])
    assert result.exit_code == 0, result.output
    assert "riskratchet baseline" not in result.stdout


def test_footer_suppressed_when_output_redirected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _high_risk_project(tmp_path)
    target = tmp_path / "out.txt"
    result = runner.invoke(
        app,
        ["scan", str(src), "--output", str(target), "--no-auto-cov", "--no-git"],
    )
    assert result.exit_code == 0, result.output
    contents = target.read_text(encoding="utf-8")
    assert "riskratchet baseline" not in contents


def test_footer_suppressed_when_quiet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _high_risk_project(tmp_path)
    result = runner.invoke(app, ["scan", str(src), "--quiet", "--no-auto-cov", "--no-git"])
    assert result.exit_code == 0, result.output
    assert "riskratchet baseline" not in result.stdout
