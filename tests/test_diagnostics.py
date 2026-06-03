"""Tests for structured diagnostics (`--verbose` / `--debug-json`, P11).

Two contracts matter here:

1. Stdout stays payload-only: enabling diagnostics must not change a single
   byte of stdout for any format.
2. The `--debug-json` envelope validates against `schemas/debug.schema.json`.
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from typer.testing import CliRunner

from riskratchet.cli import app
from riskratchet.diagnostics import Diagnostics

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"
runner = CliRunner()


def _load_debug_schema() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((SCHEMAS_DIR / "debug.schema.json").read_text(encoding="utf-8")))


def _project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "diag-fixture"\nversion = "0.0.0"\n', encoding="utf-8"
    )
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
                if x < 0:
                    return -1
                return 0
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return src


def test_envelope_has_all_categories_and_validates() -> None:
    diag = Diagnostics(command="scan")
    diag.set_coverage(mode="none", source="disabled")
    diag.set_git(enabled=False, churn_window_days=90, repo_present=False)
    diag.set_filters(include=[], exclude=[], allow=[], suppressed_functions=0)
    diag.set_analysis(
        coverage_status="missing",
        analyzed_functions=2,
        reported_functions=2,
        skipped_missing_coverage=0,
    )
    envelope = diag.to_envelope()
    Draft202012Validator(_load_debug_schema()).validate(envelope)
    assert envelope["command"] == "scan"
    assert envelope["version"] == 1
    # baseline left unset -> null, but the key is present.
    assert envelope["diagnostics"]["baseline"] is None


@pytest.mark.parametrize("fmt", ["table", "json", "markdown", "sarif", "github", "pr-comment"])
def test_verbose_does_not_touch_stdout(tmp_path: Path, fmt: str) -> None:
    src = _project(tmp_path)
    base = ["scan", str(src), "--format", fmt, "--no-auto-cov", "--no-git"]
    plain = runner.invoke(app, base)
    verbose = runner.invoke(app, [*base, "--verbose"])
    assert plain.exit_code == 0
    assert verbose.exit_code == 0
    assert verbose.stdout == plain.stdout


@pytest.mark.parametrize("fmt", ["table", "json", "markdown", "sarif", "github", "pr-comment"])
def test_debug_json_does_not_touch_stdout(tmp_path: Path, fmt: str) -> None:
    src = _project(tmp_path)
    base = ["scan", str(src), "--format", fmt, "--no-auto-cov", "--no-git"]
    plain = runner.invoke(app, base)
    debug = runner.invoke(app, [*base, "--debug-json"])
    assert debug.exit_code == 0
    assert debug.stdout == plain.stdout


def test_debug_json_file_writes_valid_envelope(tmp_path: Path) -> None:
    src = _project(tmp_path)
    out = tmp_path / "diag.json"
    plain = runner.invoke(app, ["scan", str(src), "--json", "--no-auto-cov", "--no-git"])
    result = runner.invoke(
        app,
        ["scan", str(src), "--json", "--no-auto-cov", "--no-git", "--debug-json-file", str(out)],
    )
    assert result.exit_code == 0
    # Writing the envelope to a file leaves stdout byte-identical.
    assert result.stdout == plain.stdout
    payload = json.loads(out.read_text(encoding="utf-8"))
    Draft202012Validator(_load_debug_schema()).validate(payload)
    assert payload["command"] == "scan"
    assert payload["diagnostics"]["analysis"]["reported_functions"] == 2


def test_check_debug_json_reports_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    runner.invoke(app, ["baseline", str(src), "--allow-missing-coverage", "--no-auto-cov", "--no-git"])
    out = tmp_path / "diag.json"
    result = runner.invoke(
        app,
        [
            "check",
            str(src),
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
            "--debug-json-file",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    Draft202012Validator(_load_debug_schema()).validate(payload)
    assert payload["command"] == "check"
    assert payload["diagnostics"]["baseline"]["present"] is True
    assert payload["diagnostics"]["baseline"]["entry_count"] == 2
