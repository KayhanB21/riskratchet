"""Tests for the CLI helper functions.

These exercise the small pure helpers (`_load_config`, `_resolved_paths`,
`_resolved_float`) directly so the integration tests in `test_cli.py` can
stay focused on end-to-end runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent
from typing import Any, cast

import pytest
import typer
from typer.testing import CliRunner

from riskratchet.cli import (
    _render_regressions,
    _root,
    app,
)
from riskratchet.config import (
    _load_config,
    _resolved_float,
    _resolved_paths,
)
from riskratchet.models import (
    ChurnStats,
    ComplexityStats,
    CoverageStats,
    FileStats,
    FunctionId,
    FunctionRisk,
    FunctionSpan,
    Regression,
    RegressionKind,
    RiskComponents,
)
from riskratchet.reporting import SourceLinks

runner = CliRunner()


class _Ctx:
    def __init__(self, invoked_subcommand: str | None) -> None:
        self.invoked_subcommand = invoked_subcommand

    def get_help(self) -> str:
        return "help text"


def _fn() -> FunctionRisk:
    return FunctionRisk(
        id=FunctionId("m.py", "foo"),
        span=FunctionSpan(start_line=1, end_line=3),
        is_public=True,
        complexity=ComplexityStats(cyclomatic=2),
        coverage=CoverageStats(line_coverage=0.5, branch_coverage=0.5),
        churn=ChurnStats(commits=0),
        file_stats=FileStats(path="m.py", total_lines=10, function_count=1),
        components=RiskComponents(50.0, 5.0, 50.0, 0.0, 50.0, 0.0),
        score=55.0,
        crap=4.0,
    )


def _regression() -> Regression:
    fn = _fn()
    return Regression(
        id=fn.id,
        kind=RegressionKind.REGRESSED,
        current_score=55.0,
        previous_score=40.0,
        delta=15.0,
        reason="risk grew",
        current=fn,
    )


def test_root_callback_version_help_and_subcommand_paths() -> None:
    with pytest.raises(typer.Exit) as version_exit:
        _root(cast(Any, _Ctx("scan")), version=True)
    assert version_exit.value.exit_code == 0

    with pytest.raises(typer.Exit) as help_exit:
        _root(cast(Any, _Ctx(None)), version=False)
    assert help_exit.value.exit_code == 0

    assert _root(cast(Any, _Ctx("scan")), version=False) is None


@pytest.mark.parametrize(
    ("format", "expected"),
    [
        ("json", '"regressions"'),
        ("markdown", "# riskratchet regressions"),
        ("pr-comment", "<!-- riskratchet-report -->"),
        ("github", "::warning file=m.py"),
        ("sarif", '"version": "2.1.0"'),
        ("table", "riskratchet regressions"),
    ],
)
def test_render_regressions_helper_covers_all_formats(format: str, expected: str) -> None:
    out = _render_regressions(
        [_regression()],
        format=format,
        links=SourceLinks(repo_url="https://github.com/acme/project", commit_ref="abc123"),
    )
    assert expected in out


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
    assert _resolved_paths([], {"paths": ["alpha", "beta"]}, Path.cwd(), verify_exists=False) == [
        Path("alpha"),
        Path("beta"),
    ]


def test_resolved_paths_defaults_to_cwd_when_nothing_configured() -> None:
    assert _resolved_paths([], {}, Path.cwd()) == [Path(".")]


def test_resolved_paths_prefers_explicit_argument() -> None:
    assert _resolved_paths([Path("explicit")], {"paths": ["ignored"]}, Path.cwd(), verify_exists=False) == [
        Path("explicit")
    ]


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
