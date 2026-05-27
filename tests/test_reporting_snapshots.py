"""Comprehensive snapshot tests for reporting renderers, anchored via syrupy.

Two halves:

1. **CLI-level matrix** (`test_scan_*`, `test_check_*`, `test_diff_*`):
   one syrupy snapshot per `(command x format)` pair, captured by
   invoking the real Typer CLI through `CliRunner`. These tests
   exercise the entire dispatch path (`_emit_report`, `_emit_diff`,
   `_render_regressions`, `_effective_format`) — not just the
   underlying renderer functions.

2. **Direct-renderer edge cases**: hand-built `RiskReport` /
   `Regression` / `DiffReport` fixtures from
   `tests/reporting_fixtures.py` are passed straight to the
   renderers, snapshotted via syrupy. These cover code paths the CLI
   fixture can't easily reach: overflow truncation, all four
   `RegressionKind` values, multi-target `AMBIGUOUS_RENAME`, and the
   `branch_coverage=None` formatting branch.

To regenerate after an intentional renderer change:

    uv run pytest tests/test_reporting_snapshots.py --snapshot-update

Inspect `tests/__snapshots__/test_reporting_snapshots.ambr` before
committing — first-time captures lock in whatever the renderer
*currently* produces, even if buggy.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from syrupy.assertion import SnapshotAssertion
from typer.testing import CliRunner

from reporting_fixtures import (
    diff_report_full,
    links,
    make_cli_project,
    regressions_list,
    scan_report_with_overflow,
)
from riskratchet.cli import app
from riskratchet.reporting import (
    render_diff_markdown,
    render_diff_pr_comment,
    render_diff_table,
    render_regressions_json,
    render_regressions_markdown,
    render_regressions_sarif,
    render_regressions_table,
    render_report_pr_comment,
)

runner = CliRunner()

PRIMARY_FORMATS = ["table", "json", "markdown", "pr-comment", "sarif", "github"]
SUMMARY_FORMATS = ["text", "json"]


def _write_baseline(tmp_path: Path, src: Path) -> Path:
    """Hand-build a baseline that differs from the on-disk fixture.

    The fixture scan produces three functions (`trivial`, `branchy`,
    `huge`). The baseline:
    - claims `trivial` had a high score (current is low → IMPROVED)
    - claims `branchy` had a much lower score (current is higher → REGRESSED)
    - omits `huge` (current is present → NEW)
    - includes a `ghost` function not in source (→ REMOVED)
    """
    rel = "src/m.py"
    baseline = {
        "version": "2",
        "entries": [
            {
                "path": rel,
                "qualname": "trivial",
                "score": 80.0,
                "components": {
                    "coverage_gap": 80.0,
                    "structural_complexity": 80.0,
                    "branch_gap": 80.0,
                    "churn": 80.0,
                    "public_surface": 80.0,
                    "sprawl": 80.0,
                },
            },
            {
                "path": rel,
                "qualname": "branchy",
                "score": 5.0,
                "components": {
                    "coverage_gap": 5.0,
                    "structural_complexity": 5.0,
                    "branch_gap": 5.0,
                    "churn": 5.0,
                    "public_surface": 5.0,
                    "sprawl": 5.0,
                },
            },
            {
                "path": rel,
                "qualname": "ghost",
                "score": 30.0,
                "components": {
                    "coverage_gap": 30.0,
                    "structural_complexity": 30.0,
                    "branch_gap": 30.0,
                    "churn": 30.0,
                    "public_surface": 30.0,
                    "sprawl": 30.0,
                },
            },
        ],
    }
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline, indent=2), encoding="utf-8")
    return baseline_path


@pytest.fixture
def cli_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    """Returns `(src_rel, coverage_rel, baseline_rel)`.

    Chdir's into `tmp_path` and passes relative paths to the CLI so
    riskratchet's project-root detection anchors at our fixture root,
    not the surrounding repo root. Snapshots stay stable across runs
    (no tmp_path leakage into output).
    """
    make_cli_project(tmp_path)
    _write_baseline(tmp_path, tmp_path / "src")
    monkeypatch.chdir(tmp_path)
    return Path("src"), Path("coverage.json"), Path("baseline.json")


@pytest.mark.parametrize("fmt", PRIMARY_FORMATS)
def test_scan_via_cli_snapshot(
    cli_project: tuple[Path, Path, Path],
    fmt: str,
    snapshot: SnapshotAssertion,
) -> None:
    src, coverage, _ = cli_project
    result = runner.invoke(
        app,
        [
            "scan",
            str(src),
            "--coverage",
            str(coverage),
            "--format",
            fmt,
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert result.stdout == snapshot


@pytest.mark.parametrize("fmt", SUMMARY_FORMATS)
def test_scan_summary_via_cli_snapshot(
    cli_project: tuple[Path, Path, Path],
    fmt: str,
    snapshot: SnapshotAssertion,
) -> None:
    src, coverage, _ = cli_project
    args = [
        "scan",
        str(src),
        "--coverage",
        str(coverage),
        "--summary",
        "--no-auto-cov",
        "--no-git",
    ]
    if fmt == "json":
        args.append("--json")
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.stdout
    assert result.stdout == snapshot


@pytest.mark.parametrize("fmt", PRIMARY_FORMATS)
def test_check_via_cli_snapshot(
    cli_project: tuple[Path, Path, Path],
    fmt: str,
    snapshot: SnapshotAssertion,
) -> None:
    src, coverage, baseline = cli_project
    result = runner.invoke(
        app,
        [
            "check",
            str(src),
            "--coverage",
            str(coverage),
            "--baseline",
            str(baseline),
            "--format",
            fmt,
            "--no-auto-cov",
            "--no-git",
        ],
    )
    # Exit code may be 0 or 1 depending on whether regressions are detected;
    # both are valid in a snapshot test — we pin the output, not the exit.
    assert result.exit_code in (0, 1), result.stdout
    assert result.stdout == snapshot


@pytest.mark.parametrize("fmt", SUMMARY_FORMATS)
def test_check_summary_via_cli_snapshot(
    cli_project: tuple[Path, Path, Path],
    fmt: str,
    snapshot: SnapshotAssertion,
) -> None:
    src, coverage, baseline = cli_project
    args = [
        "check",
        str(src),
        "--coverage",
        str(coverage),
        "--baseline",
        str(baseline),
        "--summary",
        "--no-auto-cov",
        "--no-git",
    ]
    if fmt == "json":
        args.append("--json")
    result = runner.invoke(app, args)
    assert result.exit_code in (0, 1), result.stdout
    assert result.stdout == snapshot


@pytest.mark.parametrize("fmt", PRIMARY_FORMATS)
def test_diff_via_cli_snapshot(
    cli_project: tuple[Path, Path, Path],
    fmt: str,
    snapshot: SnapshotAssertion,
) -> None:
    src, coverage, baseline = cli_project
    result = runner.invoke(
        app,
        [
            "diff",
            str(src),
            "--coverage",
            str(coverage),
            "--baseline",
            str(baseline),
            "--format",
            fmt,
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code in (0, 1), result.stdout
    assert result.stdout == snapshot


@pytest.mark.parametrize("fmt", SUMMARY_FORMATS)
def test_diff_summary_via_cli_snapshot(
    cli_project: tuple[Path, Path, Path],
    fmt: str,
    snapshot: SnapshotAssertion,
) -> None:
    src, coverage, baseline = cli_project
    args = [
        "diff",
        str(src),
        "--coverage",
        str(coverage),
        "--baseline",
        str(baseline),
        "--summary",
        "--no-auto-cov",
        "--no-git",
    ]
    if fmt == "json":
        args.append("--json")
    result = runner.invoke(app, args)
    assert result.exit_code in (0, 1), result.stdout
    assert result.stdout == snapshot


# Direct-renderer edge cases ---------------------------------------------------


def test_pr_comment_overflow_truncates_lower_priority(snapshot: SnapshotAssertion) -> None:
    """A 30-function low-priority report trips the `> 20 more hidden` branch."""
    rendered = render_report_pr_comment(scan_report_with_overflow(), links=links())
    assert rendered == snapshot


def test_regressions_table_covers_all_kinds(snapshot: SnapshotAssertion) -> None:
    """`RegressionKind` has 4 values; pin a fixture that includes each one."""
    rendered = render_regressions_table(regressions_list())
    assert rendered == snapshot


def test_regressions_json_covers_all_kinds(snapshot: SnapshotAssertion) -> None:
    rendered = render_regressions_json(regressions_list())
    assert rendered == snapshot


def test_regressions_markdown_covers_all_kinds(snapshot: SnapshotAssertion) -> None:
    rendered = render_regressions_markdown(regressions_list(), links=links())
    assert rendered == snapshot


def test_regressions_sarif_covers_all_kinds(snapshot: SnapshotAssertion) -> None:
    rendered = render_regressions_sarif(regressions_list())
    assert rendered == snapshot


def test_diff_table_includes_ambiguous_rename(snapshot: SnapshotAssertion) -> None:
    """`DiffStatus.AMBIGUOUS_RENAME` with multi-target previous_targets."""
    rendered = render_diff_table(diff_report_full())
    assert rendered == snapshot


def test_diff_markdown_includes_ambiguous_rename(snapshot: SnapshotAssertion) -> None:
    rendered = render_diff_markdown(diff_report_full(), links=links())
    assert rendered == snapshot


def test_diff_pr_comment_includes_ambiguous_rename(snapshot: SnapshotAssertion) -> None:
    rendered = render_diff_pr_comment(diff_report_full(), links=links())
    assert rendered == snapshot
