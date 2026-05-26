"""Tests for the top-risk dogfood script.

The script itself is a thin shell wrapper around `riskratchet scan
--top 25 --min-score 30 --format markdown|json`. These tests verify
the wrapper's canonical invocation and exercise the underlying CLI
behaviors via a small fixture so the dogfood report stays well-formed.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from textwrap import dedent

from typer.testing import CliRunner

from riskratchet.cli import app

SCRIPT = Path(__file__).resolve().parent.parent / "bin" / "dogfood-top-risk.sh"
runner = CliRunner()


def _fixture(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text(
        dedent(
            """
            def trivial():
                return 1

            def branchy(x):
                if x > 0:
                    if x > 10:
                        return "big"
                    return "small"
                if x < 0:
                    return "negative"
                return "zero"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return src


def test_dogfood_script_exists_and_is_executable() -> None:
    assert SCRIPT.exists()
    mode = SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, "dogfood script must be executable"


def test_dogfood_script_contains_canonical_scan_invocation() -> None:
    body = SCRIPT.read_text(encoding="utf-8")
    assert "riskratchet scan" in body
    assert "--top 25" in body
    assert "--min-score 30" in body
    assert "--format markdown" in body
    assert "--format json" in body
    assert "docs/top-risk.md" in body
    assert "docs/top-risk.json" in body


def test_dogfood_underlying_scan_markdown_is_well_formed(tmp_path: Path) -> None:
    src = _fixture(tmp_path)
    result = runner.invoke(
        app,
        [
            "scan",
            str(src),
            "--top",
            "25",
            "--min-score",
            "0",  # ensure the trivial fixture surfaces at least one row
            "--format",
            "markdown",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    md = result.stdout
    # Required markdown table headers from render_report_markdown:
    assert "| Severity | Score | CRAP | CC | LCov | BCov | Function | Lines |" in md
    # At least one function row
    assert md.count("\n| ") >= 1


def test_dogfood_underlying_scan_json_is_parseable(tmp_path: Path) -> None:
    src = _fixture(tmp_path)
    result = runner.invoke(
        app,
        [
            "scan",
            str(src),
            "--top",
            "25",
            "--min-score",
            "0",
            "--format",
            "json",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert "functions" in payload
    assert "summary" in payload


def test_dogfood_underlying_scan_handles_empty_top_risk_list(tmp_path: Path) -> None:
    """With a high min-score, no rows survive; the command still exits 0."""
    src = _fixture(tmp_path)
    result = runner.invoke(
        app,
        [
            "scan",
            str(src),
            "--top",
            "25",
            "--min-score",
            "100",
            "--format",
            "json",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["functions"] == []


def test_dogfood_script_resolves_repo_root_from_script_location() -> None:
    """The script cd's to the repo root using $0-relative resolution, so it
    runs identically from any cwd. Verifies the two lines that implement
    that convention."""
    body = SCRIPT.read_text(encoding="utf-8")
    assert 'cd "$root"' in body
    assert 'root="$(cd "$(dirname "$0")/.." && pwd)"' in body


def test_dogfood_script_uses_env_path_when_set(tmp_path: Path) -> None:
    """Script doesn't assume PATH; uses `uv run`. Confirm that's intact."""
    body = SCRIPT.read_text(encoding="utf-8")
    # Underlying invocations use `uv run` so the user's project venv is honored.
    assert "uv run pytest" in body
    assert "uv run riskratchet scan" in body
    # No environment override leaks expected
    assert "PATH=" not in body or os.environ.get("CI") == "true"
