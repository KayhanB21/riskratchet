"""Tests for P10 source-link parity.

When `--repo-url` and `--commit-ref` are passed, every JSON renderer
that lists functions (scan / check / diff / explain) attaches a
`source_url` field pointing at `<repo>/blob/<ref>/<path>#L<start>-L<end>`.
The field is optional in every schema so existing consumers are
unaffected.
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest
from typer.testing import CliRunner

from riskratchet.cli import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate_github_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # CLI auto-derives source links from GITHUB_* env vars when --repo-url is
    # absent; clear them so "omits source_url when links absent" tests stay
    # deterministic under GitHub Actions.
    for var in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_SHA"):
        monkeypatch.delenv(var, raising=False)


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


def test_scan_json_includes_source_url_when_links_present(tmp_path: Path) -> None:
    src = _project(tmp_path)
    result = runner.invoke(
        app,
        [
            "scan",
            str(src),
            "--json",
            "--repo-url",
            "https://github.com/acme/demo",
            "--commit-ref",
            "abc123",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["functions"], "expected at least one function"
    for fn in payload["functions"]:
        assert "source_url" in fn
        assert fn["source_url"].startswith("https://github.com/acme/demo/blob/abc123/")
        assert f"#L{fn['lines']['start']}-L{fn['lines']['end']}" in fn["source_url"]


def test_scan_json_omits_source_url_when_links_absent(tmp_path: Path) -> None:
    src = _project(tmp_path)
    result = runner.invoke(app, ["scan", str(src), "--json", "--no-auto-cov", "--no-git"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    for fn in payload["functions"]:
        assert "source_url" not in fn


def test_check_json_includes_source_url_in_regressions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
            "--repo-url",
            "https://github.com/acme/demo",
            "--commit-ref",
            "abc123",
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    # exit 1 on regressions; we expect at least one in this fixture
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["regressions"]
    for reg in payload["regressions"]:
        # source_url is optional (only when current snapshot is present),
        # but for above_threshold regressions current is always set.
        assert "source_url" in reg
        assert reg["source_url"].startswith("https://github.com/acme/demo/blob/abc123/")


def test_diff_json_includes_source_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    baseline = tmp_path / "baseline.json"
    create = runner.invoke(
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
    assert create.exit_code == 0
    result = runner.invoke(
        app,
        [
            "diff",
            str(src),
            "--baseline",
            str(baseline),
            "--json",
            "--repo-url",
            "https://github.com/acme/demo",
            "--commit-ref",
            "abc123",
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    # At least one diff entry should have source_url since the source is on disk.
    with_url = [e for e in payload["entries"] if "source_url" in e]
    assert with_url, "expected at least one diff entry with source_url"
    for entry in with_url:
        assert entry["source_url"].startswith("https://github.com/acme/demo/blob/abc123/")


def test_explain_json_includes_source_url(tmp_path: Path) -> None:
    src = _project(tmp_path)
    result = runner.invoke(
        app,
        [
            "explain",
            f"{src / 'm.py'}::branchy",
            "--json",
            "--repo-url",
            "https://github.com/acme/demo",
            "--commit-ref",
            "abc123",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    fn = payload["function"]
    assert fn["source_url"].startswith("https://github.com/acme/demo/blob/abc123/")
    assert f"#L{fn['lines']['start']}-L{fn['lines']['end']}" in fn["source_url"]


def test_scan_sarif_includes_source_url_when_links_present(tmp_path: Path) -> None:
    src = _project(tmp_path)
    result = runner.invoke(
        app,
        [
            "scan",
            str(src),
            "--format",
            "sarif",
            "--min-score",
            "0",
            "--repo-url",
            "https://github.com/acme/demo",
            "--commit-ref",
            "abc123",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    results = payload["runs"][0]["results"]
    assert results, "expected at least one SARIF result"
    for r in results:
        assert "source_url" in r["properties"]
        assert r["properties"]["source_url"].startswith("https://github.com/acme/demo/blob/abc123/")


def test_scan_sarif_omits_source_url_when_links_absent(tmp_path: Path) -> None:
    src = _project(tmp_path)
    result = runner.invoke(
        app,
        ["scan", str(src), "--format", "sarif", "--min-score", "0", "--no-auto-cov", "--no-git"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    for r in payload["runs"][0]["results"]:
        assert "source_url" not in r["properties"]


def test_scan_table_emits_source_footer_when_links_present(tmp_path: Path) -> None:
    src = _project(tmp_path)
    result = runner.invoke(
        app,
        [
            "scan",
            str(src),
            "--repo-url",
            "https://github.com/acme/demo",
            "--commit-ref",
            "abc123",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Source:" in result.stdout
    assert "https://github.com/acme/demo/blob/abc123/" in result.stdout


def test_scan_table_omits_source_footer_when_links_absent(tmp_path: Path) -> None:
    src = _project(tmp_path)
    result = runner.invoke(app, ["scan", str(src), "--no-auto-cov", "--no-git"])
    assert result.exit_code == 0, result.output
    assert "Source:" not in result.stdout
