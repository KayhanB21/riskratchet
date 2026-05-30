"""Validate CLI JSON outputs against the schemas in `schemas/`.

These tests are the contract between riskratchet and any agent (or CI script)
that parses its output. If you change a JSON field name or shape, update the
matching schema in the same PR.
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

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"
runner = CliRunner()


def _load_schema(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((SCHEMAS_DIR / name).read_text(encoding="utf-8")))


def _project(tmp_path: Path) -> Path:
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


@pytest.mark.parametrize(
    "schema_name",
    [
        "report.schema.json",
        "regressions.schema.json",
        "baseline.schema.json",
        "diff.schema.json",
        "summary.schema.json",
        "config.schema.json",
    ],
)
def test_schema_is_valid_draft_2020_12(schema_name: str) -> None:
    schema = _load_schema(schema_name)
    Draft202012Validator.check_schema(schema)


def test_scan_json_matches_report_schema(tmp_path: Path) -> None:
    src = _project(tmp_path)
    result = runner.invoke(app, ["scan", str(src), "--json", "--no-auto-cov", "--no-git"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    Draft202012Validator(_load_schema("report.schema.json")).validate(payload)


def test_check_json_matches_regressions_schema(tmp_path: Path) -> None:
    src = _project(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    runner.invoke(
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
    result = runner.invoke(
        app,
        [
            "check",
            str(src),
            "--baseline",
            str(baseline_path),
            "--json",
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    Draft202012Validator(_load_schema("regressions.schema.json")).validate(payload)


def test_check_fail_above_json_matches_regressions_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No-baseline mode reuses the regressions envelope with kind=above_threshold."""
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
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    Draft202012Validator(_load_schema("regressions.schema.json")).validate(payload)


def test_diff_json_matches_diff_schema(tmp_path: Path) -> None:
    src = _project(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    runner.invoke(
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
    result = runner.invoke(
        app,
        [
            "diff",
            str(src),
            "--baseline",
            str(baseline_path),
            "--json",
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    Draft202012Validator(_load_schema("diff.schema.json")).validate(payload)


@pytest.mark.parametrize("command", ["scan", "check", "diff"])
def test_summary_json_matches_summary_schema(tmp_path: Path, command: str) -> None:
    src = _project(tmp_path)
    args = [command, str(src), "--summary", "--json", "--no-auto-cov", "--no-git"]
    if command in {"check", "diff"}:
        baseline_path = tmp_path / "baseline.json"
        runner.invoke(
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
        args.extend(["--baseline", str(baseline_path), "--allow-missing-coverage"])
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    Draft202012Validator(_load_schema("summary.schema.json")).validate(payload)


def test_config_show_json_matches_config_schema(tmp_path: Path) -> None:
    config = tmp_path / "pyproject.toml"
    config.write_text(
        dedent(
            """
            [tool.riskratchet]
            paths = ["src"]

            [tool.riskratchet.groups]
            core = "src/core"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["config", "show", "--config", str(config), "--json"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    Draft202012Validator(_load_schema("config.schema.json")).validate(payload)


def test_baseline_file_matches_baseline_schema(tmp_path: Path) -> None:
    src = _project(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    result = runner.invoke(
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
    assert result.exit_code == 0
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    Draft202012Validator(_load_schema("baseline.schema.json")).validate(payload)


def test_baseline_includes_signature_field(tmp_path: Path) -> None:
    """Schema allows signature; the produced baseline emits it for every entry."""
    src = _project(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    runner.invoke(
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
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert payload["entries"], "fixture project should have at least one function"
    for entry in payload["entries"]:
        assert "signature" in entry
        assert isinstance(entry["signature"], str)


def test_diff_schema_allows_ambiguous_rename_status() -> None:
    """The diff schema enum includes ambiguous_rename and accepts the new fields."""
    schema = _load_schema("diff.schema.json")
    status_enum = schema["properties"]["entries"]["items"]["properties"]["status"]["enum"]
    assert "ambiguous_rename" in status_enum
    summary_required = schema["properties"]["summary"]["required"]
    assert "ambiguous_rename" in summary_required
    entry_props = schema["properties"]["entries"]["items"]["properties"]
    assert "previous_targets" in entry_props
    assert "match_confidence" in entry_props


def test_diff_json_with_ambiguous_rename_matches_schema(tmp_path: Path) -> None:
    """End-to-end: a project with an ambiguous rename produces schema-valid JSON."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text(
        dedent(
            """
            def one():
                return 42

            def two():
                return 42
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    baseline_path = tmp_path / "baseline.json"
    runner.invoke(
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
    # Replace both functions with one new function whose body matches both
    # baseline entries; this is the canonical ambiguous-rename trigger.
    (src / "m.py").write_text(
        dedent(
            """
            def merged():
                return 42
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "diff",
            str(src),
            "--baseline",
            str(baseline_path),
            "--json",
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    Draft202012Validator(_load_schema("diff.schema.json")).validate(payload)
    statuses = {e["status"] for e in payload["entries"]}
    assert "ambiguous_rename" in statuses
