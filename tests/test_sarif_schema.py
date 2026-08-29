"""Validate `--format sarif` against the normative SARIF 2.1.0 schema.

The other JSON outputs are checked against hand-written schemas in `schemas/`,
but SARIF is not riskratchet's format to define: GitHub's code-scanning upload
rejects a log that does not validate, and until now the only guards were an
assertion on the `$schema` string and a frozen snapshot. A snapshot pins
whatever the renderer currently emits, correct or not — which is how the
`--format github` escaping stayed broken through twenty releases with a
committed snapshot of it.

The schema is vendored under `tests/vendor/` rather than added to `schemas/`,
which holds the formats riskratchet itself owns.
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

SARIF_SCHEMA = Path(__file__).resolve().parent / "vendor" / "sarif-2.1.0.schema.json"
runner = CliRunner()


@pytest.fixture(scope="module")
def sarif_validator() -> Draft202012Validator:
    schema = cast(dict[str, Any], json.loads(SARIF_SCHEMA.read_text(encoding="utf-8")))
    return Draft202012Validator(schema)


def _project(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text("[tool.riskratchet]\n", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text(
        dedent(
            """
            def trivial():
                return 1

            def branchy(a, b, c, d, e):
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
                return 0
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return src


def _run(args: list[str], *, config: Path) -> tuple[int, str]:
    # `scan` never fails on missing coverage, so it has no
    # `--allow-missing-coverage` to accept.
    extra = [] if args[0] == "scan" else ["--allow-missing-coverage"]
    result = runner.invoke(
        app,
        [*args, "--config", str(config), *extra, "--no-auto-cov", "--no-git"],
    )
    return result.exit_code, result.stdout


def _assert_valid(validator: Draft202012Validator, payload: str) -> dict[str, Any]:
    log = cast(dict[str, Any], json.loads(payload))
    errors = sorted(validator.iter_errors(log), key=lambda e: list(e.path))
    assert not errors, "\n".join(f"{list(e.path)}: {e.message}" for e in errors[:10])
    return log


def test_scan_sarif_validates(tmp_path: Path, sarif_validator: Draft202012Validator) -> None:
    src = _project(tmp_path)
    code, out = _run(
        ["scan", str(src), "--format", "sarif", "--min-score", "0"],
        config=tmp_path / "pyproject.toml",
    )
    assert code == 0, out
    log = _assert_valid(sarif_validator, out)
    assert log["version"] == "2.1.0"
    assert log["runs"][0]["results"], "a scan with findings emitted no SARIF results"


def test_check_sarif_validates(tmp_path: Path, sarif_validator: Draft202012Validator) -> None:
    src = _project(tmp_path)
    code, out = _run(
        ["check", str(src), "--fail-above", "1", "--format", "sarif"],
        config=tmp_path / "pyproject.toml",
    )
    assert code == 1, out
    log = _assert_valid(sarif_validator, out)
    assert log["runs"][0]["results"]


def test_empty_sarif_log_validates(tmp_path: Path, sarif_validator: Draft202012Validator) -> None:
    """A clean run still uploads: an empty `results` array must be valid."""
    src = _project(tmp_path)
    code, out = _run(
        ["check", str(src), "--fail-above", "99", "--format", "sarif"],
        config=tmp_path / "pyproject.toml",
    )
    assert code == 0, out
    log = _assert_valid(sarif_validator, out)
    assert log["runs"][0]["results"] == []


def test_every_result_rule_is_declared(tmp_path: Path, sarif_validator: Draft202012Validator) -> None:
    """`ruleId` must resolve in `tool.driver.rules`, or GitHub drops the result."""
    src = _project(tmp_path)
    code, out = _run(
        ["scan", str(src), "--format", "sarif", "--min-score", "0"],
        config=tmp_path / "pyproject.toml",
    )
    assert code == 0, out
    log = _assert_valid(sarif_validator, out)
    declared = {rule["id"] for rule in log["runs"][0]["tool"]["driver"]["rules"]}
    used = {result["ruleId"] for result in log["runs"][0]["results"]}
    assert used <= declared, f"undeclared rules: {sorted(used - declared)}"
