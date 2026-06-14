"""JSON schema + markdown stability snapshots for the CLI output.

The JSON schema test locks the top-level shape and the per-function shape
so downstream PR-bots can rely on it. The markdown snapshot is a
golden-file comparison, lightly normalized for floating-point noise.

If either test fails, you have either (a) intentionally changed the
output contract, in which case update the assertion or the golden file;
or (b) accidentally changed a field name, in which case revert.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from textwrap import dedent

import pytest
from syrupy.assertion import SnapshotAssertion
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


REQUIRED_TOP_LEVEL = {"$schema", "version", "summary", "functions"}
REQUIRED_SUMMARY = {
    "total_functions",
    "analyzed_functions",
    "emitted_functions",
    "total_files",
    "coverage_status",
    "suppressed_functions",
    "skipped_missing_coverage",
    "by_severity",
    "groups",
}
REQUIRED_FUNCTION = {
    "path",
    "qualname",
    "severity",
    "score",
    "crap",
    "complexity",
    "line_coverage",
    "branch_coverage",
    "churn_commits",
    "is_public",
    "group",
    "language",
    "lines",
    "components",
}
REQUIRED_COMPONENTS = {
    "coverage_gap",
    "structural_complexity",
    "branch_gap",
    "churn",
    "public_surface",
    "sprawl",
}
ALLOWED_SEVERITIES = {"low", "medium", "high", "critical"}


def test_scan_json_schema_is_stable(tmp_path: Path) -> None:
    src = _project(tmp_path)
    result = runner.invoke(app, ["scan", str(src), "--format", "json", "--no-auto-cov", "--no-git"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)

    assert REQUIRED_TOP_LEVEL.issubset(payload.keys())

    summary = payload["summary"]
    assert REQUIRED_SUMMARY.issubset(summary.keys())
    assert isinstance(summary["total_functions"], int)
    assert isinstance(summary["analyzed_functions"], int)
    assert isinstance(summary["emitted_functions"], int)
    assert isinstance(summary["total_files"], int)
    assert summary["coverage_status"] in {"present", "missing"}
    assert isinstance(summary["suppressed_functions"], int)
    assert isinstance(summary["skipped_missing_coverage"], int)
    assert set(summary["by_severity"].keys()) == ALLOWED_SEVERITIES
    for count in summary["by_severity"].values():
        assert isinstance(count, int)
    assert isinstance(summary["groups"], dict)

    functions = payload["functions"]
    assert isinstance(functions, list)
    assert len(functions) == 2

    for fn in functions:
        assert REQUIRED_FUNCTION.issubset(fn.keys())
        assert fn["severity"] in ALLOWED_SEVERITIES
        assert 0.0 <= fn["score"] <= 100.0
        assert isinstance(fn["complexity"], int)
        assert 0.0 <= fn["line_coverage"] <= 1.0
        assert fn["branch_coverage"] is None or 0.0 <= fn["branch_coverage"] <= 1.0
        assert isinstance(fn["churn_commits"], int)
        assert isinstance(fn["is_public"], bool)
        assert fn["group"] is None or isinstance(fn["group"], str)
        assert set(fn["lines"].keys()) == {"start", "end"}
        assert set(fn["components"].keys()) == REQUIRED_COMPONENTS


def _normalize_markdown(text: str) -> str:
    """Strip path noise and round floats so the snapshot is stable."""
    # Replace any prefix (including no prefix) with a placeholder.
    text = re.sub(r"`(?:[^`]*?/)?src/m\.py", "`TMP/src/m.py", text)
    return text


def test_scan_markdown_snapshot_is_stable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    snapshot: SnapshotAssertion,
) -> None:
    src = _project(tmp_path)
    # chdir into tmp_path so the snapshot is hermetic: avoids picking up a
    # coverage.json or pyproject.toml from the repo root. Clear GitHub Actions
    # env vars so default source-linking does not change this plain markdown
    # snapshot in CI.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_SERVER_URL", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    result = runner.invoke(
        app,
        ["scan", str(src), "--format", "markdown", "--no-auto-cov", "--no-git"],
    )
    assert result.exit_code == 0, result.stdout
    assert _normalize_markdown(result.stdout) == snapshot


def test_scan_pr_comment_snapshot_is_stable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    snapshot: SnapshotAssertion,
) -> None:
    src = _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        [
            "scan",
            str(src),
            "--format",
            "pr-comment",
            "--repo-url",
            "https://github.com/acme/project",
            "--commit-ref",
            "abc123",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert result.stdout == snapshot
