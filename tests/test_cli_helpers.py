"""Tests for the CLI helper functions.

These exercise the small pure helpers (`_load_config`, `_resolved_paths`,
`_resolved_optional`, `_resolved_float`) directly so the integration tests
in `test_cli.py` can stay focused on end-to-end runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

from typer.testing import CliRunner

from riskratchet.cli import (
    _load_config,
    _resolved_float,
    _resolved_optional,
    _resolved_paths,
    app,
)

runner = CliRunner()


def test_load_config_returns_empty_when_pyproject_missing(tmp_path: Path) -> None:
    assert _load_config(tmp_path / "missing.toml") == {}


def test_load_config_reads_tool_section(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        dedent(
            """
            [tool.riskratchet]
            paths = ["src"]
            fail_new_above = 40
            """
        ).strip(),
        encoding="utf-8",
    )
    cfg = _load_config(pyproject)
    assert cfg.get("paths") == ["src"]
    assert cfg.get("fail_new_above") == 40


def test_load_config_warns_and_returns_empty_on_malformed_toml(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.riskratchet\nbroken =", encoding="utf-8")
    assert _load_config(pyproject) == {}


def test_load_config_ignores_non_mapping_tool_section(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        dedent(
            """
            [tool]
            riskratchet = "not a table"
            """
        ).strip(),
        encoding="utf-8",
    )
    assert _load_config(pyproject) == {}


def test_resolved_paths_falls_back_to_config() -> None:
    assert _resolved_paths([], {"paths": ["alpha", "beta"]}) == [Path("alpha"), Path("beta")]


def test_resolved_paths_defaults_to_cwd_when_nothing_configured() -> None:
    assert _resolved_paths([], {}) == [Path(".")]


def test_resolved_paths_prefers_explicit_argument() -> None:
    assert _resolved_paths([Path("explicit")], {"paths": ["ignored"]}) == [Path("explicit")]


def test_resolved_optional_prefers_explicit_value(tmp_path: Path) -> None:
    explicit = tmp_path / "x.json"
    explicit.write_text("{}", encoding="utf-8")
    assert _resolved_optional(explicit, "ignored.json") == explicit


def test_resolved_optional_returns_none_when_default_is_not_pathlike() -> None:
    assert _resolved_optional(None, 42) is None
    assert _resolved_optional(None, None) is None


def test_resolved_optional_accepts_existing_path_default(tmp_path: Path) -> None:
    existing = tmp_path / "x.json"
    existing.write_text("{}", encoding="utf-8")
    assert _resolved_optional(None, existing) == existing


def test_resolved_optional_ignores_missing_string_default(tmp_path: Path) -> None:
    assert _resolved_optional(None, str(tmp_path / "missing.json")) is None


def test_resolved_float_prefers_cli_value() -> None:
    assert _resolved_float(12.5, 50, default=99.0) == 12.5


def test_resolved_float_uses_cfg_value_when_cli_missing() -> None:
    assert _resolved_float(None, 30, default=99.0) == 30.0


def test_resolved_float_falls_back_to_default_when_cfg_not_numeric() -> None:
    assert _resolved_float(None, "not a number", default=99.0) == 99.0


def test_no_args_prints_help() -> None:
    # Typer's `no_args_is_help=True` exits with status 2 (usage). Either way
    # the user should see the usage banner.
    result = runner.invoke(app, [])
    combined = result.stdout + (result.stderr if result.stderr_bytes is not None else "")
    assert "Usage" in combined or "usage" in combined


def test_explain_missing_separator_returns_usage_error(tmp_path: Path) -> None:
    # Even if the function exists in some file, missing `::` is a usage error.
    result = runner.invoke(app, ["explain", "no-separator", "--no-auto-cov", "--no-git"])
    assert result.exit_code != 0


def test_scan_invalid_format_returns_usage_error(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text("def f(): pass\n", encoding="utf-8")
    result = runner.invoke(app, ["scan", str(src), "--format", "xml", "--no-auto-cov", "--no-git"])
    assert result.exit_code != 0


def test_scan_writes_to_output_file(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text("def f(): return 1\n", encoding="utf-8")
    target = tmp_path / "report.json"
    result = runner.invoke(
        app,
        ["scan", str(src), "--format", "json", "--output", str(target), "--no-auto-cov", "--no-git"],
    )
    assert result.exit_code == 0
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert "functions" in payload


def test_check_emits_markdown_to_output_when_clean(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text("def f(): return 1\n", encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    runner.invoke(
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
    out = tmp_path / "out.md"
    result = runner.invoke(
        app,
        [
            "check",
            str(src),
            "--baseline",
            str(baseline),
            "--format",
            "markdown",
            "--output",
            str(out),
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert out.read_text(encoding="utf-8")
