"""Smoke tests for the Typer CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

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


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip()


def test_scan_succeeds_and_prints_summary(tmp_path: Path) -> None:
    src = _project(tmp_path)
    result = runner.invoke(app, ["scan", str(src), "--no-git"])
    assert result.exit_code == 0
    assert "Summary" in result.stdout


def test_scan_json_output_is_valid(tmp_path: Path) -> None:
    src = _project(tmp_path)
    result = runner.invoke(app, ["scan", str(src), "--format", "json", "--no-git"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert "functions" in payload
    assert "summary" in payload


def test_baseline_writes_file(tmp_path: Path) -> None:
    src = _project(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    result = runner.invoke(
        app,
        ["baseline", str(src), "--output", str(baseline_path), "--no-git"],
    )
    assert result.exit_code == 0
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert payload["version"] == "1"
    assert isinstance(payload["entries"], list)
    assert len(payload["entries"]) >= 1


def test_check_against_clean_baseline_exits_zero(tmp_path: Path) -> None:
    src = _project(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    runner.invoke(app, ["baseline", str(src), "--output", str(baseline_path), "--no-git"])
    result = runner.invoke(
        app,
        ["check", str(src), "--baseline", str(baseline_path), "--no-git"],
    )
    assert result.exit_code == 0, result.stdout


def test_check_flags_new_risky_function(tmp_path: Path) -> None:
    src = _project(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    runner.invoke(app, ["baseline", str(src), "--output", str(baseline_path), "--no-git"])

    risky_file = src / "risky.py"
    risky_file.write_text(
        dedent(
            """
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
    result = runner.invoke(
        app,
        [
            "check",
            str(src),
            "--baseline",
            str(baseline_path),
            "--fail-new-above",
            "10",
            "--no-git",
        ],
    )
    assert result.exit_code == 1, result.stdout


def test_check_missing_baseline_returns_exit_2(tmp_path: Path) -> None:
    src = _project(tmp_path)
    result = runner.invoke(
        app,
        ["check", str(src), "--baseline", str(tmp_path / "nope.json"), "--no-git"],
    )
    assert result.exit_code == 2


def test_explain_renders_for_known_function(tmp_path: Path) -> None:
    src = _project(tmp_path)
    target = f"{src.as_posix()}/m.py::branchy"
    result = runner.invoke(app, ["explain", target, "--no-git"])
    assert result.exit_code == 0, result.stdout
    assert "branchy" in result.stdout
    assert "complexity" in result.stdout


def test_explain_unknown_function_returns_exit_2(tmp_path: Path) -> None:
    src = _project(tmp_path)
    target = f"{src.as_posix()}/m.py::ghost"
    result = runner.invoke(app, ["explain", target, "--no-git"])
    assert result.exit_code == 2


def test_scan_json_flag_matches_format_json(tmp_path: Path) -> None:
    src = _project(tmp_path)
    via_flag = runner.invoke(app, ["scan", str(src), "--json", "--no-git"])
    via_format = runner.invoke(app, ["scan", str(src), "--format", "json", "--no-git"])
    assert via_flag.exit_code == 0
    assert via_format.exit_code == 0
    assert json.loads(via_flag.stdout) == json.loads(via_format.stdout)


def test_scan_json_flag_overrides_format(tmp_path: Path) -> None:
    src = _project(tmp_path)
    result = runner.invoke(app, ["scan", str(src), "--format", "table", "--json", "--no-git"])
    assert result.exit_code == 0
    json.loads(result.stdout)


def test_scan_quiet_suppresses_summary(tmp_path: Path) -> None:
    src = _project(tmp_path)
    loud = runner.invoke(app, ["scan", str(src), "--no-git"])
    quiet = runner.invoke(app, ["scan", str(src), "--quiet", "--no-git"])
    assert loud.exit_code == 0
    assert quiet.exit_code == 0
    assert "Summary" in loud.stdout
    assert "Summary" not in quiet.stdout


def test_check_json_flag_produces_regressions_payload(tmp_path: Path) -> None:
    src = _project(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    runner.invoke(app, ["baseline", str(src), "--output", str(baseline_path), "--no-git"])
    result = runner.invoke(
        app,
        ["check", str(src), "--baseline", str(baseline_path), "--json", "--no-git"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert "regressions" in payload
    assert payload["regressions"] == []
