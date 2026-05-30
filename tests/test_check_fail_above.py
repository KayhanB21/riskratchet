"""Tests for `check --fail-above N` (P28).

The no-baseline gate: when `--fail-above N` is given and no baseline
resolves, `check` emits a regression-style envelope where every function
with `score > N` is reported with `kind=above_threshold`. When a baseline
IS resolved, `--fail-above` is ignored with a stderr warning and the
existing baseline gate runs.

Each test chdirs into `tmp_path` so config discovery does not walk up
into the riskratchet repo's own `pyproject.toml` / `.riskratchet.json`.
"""

from __future__ import annotations

import json
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

            def branchy(x):
                if x > 0:
                    return 1
                if x < 0:
                    return -1
                return 0
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return tmp_path / "src"


def test_fail_above_no_baseline_clean_exits_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    result = runner.invoke(
        app,
        ["check", str(src), "--fail-above", "100", "--allow-missing-coverage", "--no-auto-cov", "--no-git"],
    )
    assert result.exit_code == 0, result.output


def test_fail_above_no_baseline_dirty_exits_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    result = runner.invoke(
        app,
        ["check", str(src), "--fail-above", "5", "--allow-missing-coverage", "--no-auto-cov", "--no-git"],
    )
    assert result.exit_code == 1, result.output


def test_fail_above_json_emits_above_threshold_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    result = runner.invoke(
        app,
        [
            "check",
            str(src),
            "--fail-above",
            "5",
            "--json",
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["regressions"], "expected at least one regression"
    for entry in payload["regressions"]:
        assert entry["kind"] == "above_threshold"
        assert entry["previous_score"] is None
        assert entry["delta"] is None
        assert "threshold 5.0" in entry["reason"]


def test_fail_above_with_baseline_warns_and_runs_baseline_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    baseline_result = runner.invoke(
        app,
        [
            "baseline",
            str(src),
            "--output",
            str(baseline_path),
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert baseline_result.exit_code == 0, baseline_result.output
    result = runner.invoke(
        app,
        [
            "check",
            str(src),
            "--baseline",
            str(baseline_path),
            "--fail-above",
            "1",
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "--fail-above ignored" in result.output


def test_missing_baseline_and_no_fail_above_returns_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    result = runner.invoke(
        app,
        [
            "check",
            str(src),
            "--baseline",
            str(tmp_path / "nope.json"),
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 2
    assert "--fail-above" in result.output


def test_fail_above_out_of_range_returns_exit_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    too_high = runner.invoke(
        app,
        ["check", str(src), "--fail-above", "150", "--allow-missing-coverage", "--no-auto-cov", "--no-git"],
    )
    assert too_high.exit_code == 2
    too_low = runner.invoke(
        app,
        ["check", str(src), "--fail-above", "0", "--allow-missing-coverage", "--no-auto-cov", "--no-git"],
    )
    assert too_low.exit_code == 2


def test_fail_above_no_baseline_rejects_pr_comment_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    result = runner.invoke(
        app,
        [
            "check",
            str(src),
            "--fail-above",
            "50",
            "--format",
            "pr-comment",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 2
    assert "pr-comment requires a baseline" in result.output


def test_fail_above_no_baseline_summary_text_works(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    result = runner.invoke(
        app,
        [
            "check",
            str(src),
            "--fail-above",
            "5",
            "--summary",
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "regressions=" in result.stdout


def test_fail_above_via_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        dedent(
            """
            [tool.riskratchet]
            paths = ["src"]
            fail_above = 5
            allow_missing_coverage = true
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["check", "--config", str(tmp_path / "pyproject.toml"), "--no-auto-cov", "--no-git"],
    )
    assert result.exit_code == 1, result.output


def test_fail_above_emits_threshold_hint_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    result = runner.invoke(
        app,
        ["check", str(src), "--fail-above", "5", "--allow-missing-coverage", "--no-auto-cov", "--no-git"],
    )
    assert result.exit_code == 1
    assert "scored above --fail-above" in result.output
    assert "Raise the threshold" in result.output
    assert "Adopt a baseline" in result.output
