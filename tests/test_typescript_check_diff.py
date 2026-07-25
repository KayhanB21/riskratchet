"""check / diff over TypeScript, routed through pipeline.build_report (B5, 0.3.0).

check and diff gain --typescript and analyze TS alongside Python. The comparison machinery
(baseline.compare / diff / match_rename) is already language-neutral, so a scored TS function flows
through the ratchet exactly like a Python one — flagged new, gated by --fail-new-above, and diffed.
(TS entries in the *baseline* itself land in B6; here the baseline is Python-only, so TS reads new.)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from riskratchet.cli import app

runner = CliRunner()


def _project(tmp_path: Path) -> Path:
    (tmp_path / "m.py").write_text("def f(x):\n    return x\n", encoding="utf-8")
    (tmp_path / "s.ts").write_text(
        "export function g(a: number) { return a > 0 ? a : -a; }\n", encoding="utf-8"
    )
    return tmp_path


def _baseline(tmp_path: Path) -> Path:
    out = tmp_path / ".rr.json"
    result = runner.invoke(
        app, ["baseline", str(tmp_path), "--output", str(out), "--no-git", "--no-auto-cov"]
    )
    assert result.exit_code == 0, result.stderr
    return out


def test_diff_typescript_reports_new_ts_function(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    _project(tmp_path)
    baseline = _baseline(tmp_path)  # Python-only baseline (baseline --typescript lands in B6)
    result = runner.invoke(
        app,
        [
            "diff",
            str(tmp_path),
            "--typescript",
            "--baseline",
            str(baseline),
            "--no-git",
            "--no-auto-cov",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stderr
    entries: dict[str, Any] = {e["qualname"]: e for e in json.loads(result.stdout)["entries"]}
    assert entries["g"]["status"] == "new"  # the TS function, absent from the Python baseline
    assert entries["f"]["status"] == "unchanged"  # the Python function


def test_check_typescript_new_function_is_gated(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    _project(tmp_path)
    baseline = _baseline(tmp_path)
    # g scores ~41; --fail-new-above 10 makes the new TS function fail the gate → exit 1.
    result = runner.invoke(
        app,
        [
            "check",
            str(tmp_path),
            "--typescript",
            "--baseline",
            str(baseline),
            "--no-git",
            "--no-auto-cov",
            "--fail-new-above",
            "10",
        ],
    )
    assert result.exit_code == 1
    assert "g" in result.stdout or "g" in result.stderr


def test_check_without_typescript_ignores_ts(tmp_path: Path) -> None:
    # Without --typescript, the TS file is invisible to check (Python-only), so the gate passes.
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    _project(tmp_path)
    baseline = _baseline(tmp_path)
    result = runner.invoke(
        app,
        [
            "check",
            str(tmp_path),
            "--baseline",
            str(baseline),
            "--no-git",
            "--no-auto-cov",
            "--fail-new-above",
            "10",
        ],
    )
    assert result.exit_code == 0, result.stderr
