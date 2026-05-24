"""Smoke tests for the Typer CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest
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


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip()


def test_scan_succeeds_and_prints_summary(tmp_path: Path) -> None:
    src = _project(tmp_path)
    result = runner.invoke(app, ["scan", str(src), "--no-auto-cov", "--no-git"])
    assert result.exit_code == 0
    assert "Summary" in result.stdout


def test_scan_json_output_is_valid(tmp_path: Path) -> None:
    src = _project(tmp_path)
    result = runner.invoke(app, ["scan", str(src), "--format", "json", "--no-auto-cov", "--no-git"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert "functions" in payload
    assert "summary" in payload


def test_scan_summary_outputs_text_only(tmp_path: Path) -> None:
    src = _project(tmp_path)
    result = runner.invoke(app, ["scan", str(src), "--summary", "--no-auto-cov", "--no-git"])
    assert result.exit_code == 0
    assert result.stdout.startswith("scan functions=2 analyzed=2 emitted=2 files=1 coverage=")
    assert "| Severity |" not in result.stdout


def test_scan_summary_json_outputs_summary_envelope(tmp_path: Path) -> None:
    src = _project(tmp_path)
    result = runner.invoke(app, ["scan", str(src), "--summary", "--json", "--no-auto-cov", "--no-git"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "scan"
    assert set(payload) == {"$schema", "version", "command", "summary"}
    assert payload["summary"]["total_functions"] == 2


def test_scan_sarif_output_is_valid(tmp_path: Path) -> None:
    src = _project(tmp_path)
    result = runner.invoke(app, ["scan", str(src), "--format", "sarif", "--no-auto-cov", "--no-git"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["version"] == "2.1.0"
    run = payload["runs"][0]
    assert run["tool"]["driver"]["name"] == "riskratchet"
    assert len(run["results"]) == 2
    location = run["results"][0]["locations"][0]["physicalLocation"]
    assert not location["artifactLocation"]["uri"].startswith("/")


def test_baseline_writes_file(tmp_path: Path) -> None:
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
    assert payload["version"] == "2"
    assert isinstance(payload["entries"], list)
    assert len(payload["entries"]) >= 1


def test_baseline_rejects_missing_configured_coverage(tmp_path: Path) -> None:
    src = _project(tmp_path)
    config = tmp_path / "pyproject.toml"
    config.write_text("[tool.riskratchet]\ncoverage = 'missing.json'\n", encoding="utf-8")
    result = runner.invoke(app, ["baseline", str(src), "--config", str(config), "--no-auto-cov", "--no-git"])
    assert result.exit_code == 2
    assert "coverage data is required" in result.stderr


def test_baseline_allow_missing_coverage_preserves_no_coverage_mode(tmp_path: Path) -> None:
    src = _project(tmp_path)
    config = tmp_path / "pyproject.toml"
    config.write_text("[tool.riskratchet]\ncoverage = 'missing.json'\n", encoding="utf-8")
    baseline_path = tmp_path / "baseline.json"
    result = runner.invoke(
        app,
        [
            "baseline",
            str(src),
            "--config",
            str(config),
            "--output",
            str(baseline_path),
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert baseline_path.exists()


def test_check_against_clean_baseline_exits_zero(tmp_path: Path) -> None:
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
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 0, result.stdout


def test_check_fail_existing_above_flags_current_debt(tmp_path: Path) -> None:
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
            "--fail-existing-above",
            "10",
            "--allow-missing-coverage",
            "--json",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 1, result.stdout
    payload = json.loads(result.stdout)
    assert payload["regressions"][0]["kind"] == "existing_above_threshold"


def test_check_sarif_against_clean_baseline_exits_zero_with_empty_results(tmp_path: Path) -> None:
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
            "--format",
            "sarif",
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["runs"][0]["results"] == []


def test_check_summary_keeps_failure_exit_code(tmp_path: Path) -> None:
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
    (src / "risky.py").write_text(
        "def risky(a, b, c, d, e, f):\n"
        "    if a: return 1\n"
        "    if b: return 2\n"
        "    if c: return 3\n"
        "    if d: return 4\n"
        "    if e: return 5\n"
        "    if f: return 6\n"
        "    return 0\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "check",
            str(src),
            "--baseline",
            str(baseline_path),
            "--fail-new-above",
            "10",
            "--summary",
            "--json",
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["command"] == "check"
    assert payload["summary"]["regressions"] == 1


def test_check_flags_new_risky_function(tmp_path: Path) -> None:
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

    risky_file = src / "risky.py"
    risky_file.write_text(
        dedent(
            """
            def risky(a, b, c, d, e, f, g, h, i, j):
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
                if f:
                    return 6
                if g:
                    return 7
                if h:
                    return 8
                if i:
                    return 9
                if j:
                    return 10
                return 0
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "check",
            str(src),
            "--baseline",
            str(baseline_path),
            "--fail-new-above",
            "10",
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 1, result.stdout


def test_check_sarif_reports_regressions(tmp_path: Path) -> None:
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

    (src / "risky.py").write_text(
        dedent(
            """
            def risky(a, b, c, d, e, f, g, h, i, j):
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
                if f:
                    return 6
                if g:
                    return 7
                if h:
                    return 8
                if i:
                    return 9
                if j:
                    return 10
                return 0
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "check",
            str(src),
            "--baseline",
            str(baseline_path),
            "--fail-new-above",
            "10",
            "--format",
            "sarif",
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 1, result.stdout
    payload = json.loads(result.stdout)
    results = payload["runs"][0]["results"]
    assert len(results) == 1
    assert results[0]["ruleId"] == "riskratchet.regression"
    assert "riskratchet.regression" in {rule["id"] for rule in payload["runs"][0]["tool"]["driver"]["rules"]}


def test_check_missing_baseline_returns_exit_2(tmp_path: Path) -> None:
    src = _project(tmp_path)
    result = runner.invoke(
        app,
        ["check", str(src), "--baseline", str(tmp_path / "nope.json"), "--no-auto-cov", "--no-git"],
    )
    assert result.exit_code == 2


def test_explain_renders_for_known_function(tmp_path: Path) -> None:
    src = _project(tmp_path)
    target = f"{src.as_posix()}/m.py::branchy"
    result = runner.invoke(app, ["explain", target, "--no-auto-cov", "--no-git"])
    assert result.exit_code == 0, result.stdout
    assert "branchy" in result.stdout
    assert "complexity" in result.stdout


def test_explain_unknown_function_returns_exit_2(tmp_path: Path) -> None:
    src = _project(tmp_path)
    target = f"{src.as_posix()}/m.py::ghost"
    result = runner.invoke(app, ["explain", target, "--no-auto-cov", "--no-git"])
    assert result.exit_code == 2


def test_scan_json_flag_matches_format_json(tmp_path: Path) -> None:
    src = _project(tmp_path)
    via_flag = runner.invoke(app, ["scan", str(src), "--json", "--no-auto-cov", "--no-git"])
    via_format = runner.invoke(app, ["scan", str(src), "--format", "json", "--no-auto-cov", "--no-git"])
    assert via_flag.exit_code == 0
    assert via_format.exit_code == 0
    assert json.loads(via_flag.stdout) == json.loads(via_format.stdout)


def test_scan_json_flag_overrides_format(tmp_path: Path) -> None:
    src = _project(tmp_path)
    result = runner.invoke(
        app, ["scan", str(src), "--format", "table", "--json", "--no-auto-cov", "--no-git"]
    )
    assert result.exit_code == 0
    json.loads(result.stdout)


def test_scan_quiet_suppresses_summary(tmp_path: Path) -> None:
    src = _project(tmp_path)
    loud = runner.invoke(app, ["scan", str(src), "--no-auto-cov", "--no-git"])
    quiet = runner.invoke(app, ["scan", str(src), "--quiet", "--no-auto-cov", "--no-git"])
    assert loud.exit_code == 0
    assert quiet.exit_code == 0
    assert "Summary" in loud.stdout
    assert "Summary" not in quiet.stdout


def test_scan_fail_above_exits_one(tmp_path: Path) -> None:
    src = _project(tmp_path)
    result = runner.invoke(
        app,
        ["scan", str(src), "--fail-above", "10", "--no-auto-cov", "--no-git"],
    )
    assert result.exit_code == 1


def test_scan_fail_severity_exits_one(tmp_path: Path) -> None:
    src = _project(tmp_path)
    (src / "risky.py").write_text(
        dedent(
            """
            def risky(a, b, c, d, e, f, g, h, i, j):
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
                if f:
                    return 6
                if g:
                    return 7
                if h:
                    return 8
                if i:
                    return 9
                if j:
                    return 10
                return 0
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["scan", str(src), "--fail-severity", "high", "--no-auto-cov", "--no-git"],
    )
    assert result.exit_code == 1


def test_scan_top_filters_json(tmp_path: Path) -> None:
    src = _project(tmp_path)
    result = runner.invoke(
        app,
        ["scan", str(src), "--json", "--top", "1", "--no-auto-cov", "--no-git"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(payload["functions"]) == 1
    assert payload["summary"]["emitted_functions"] == 1


def test_scan_min_score_filters_json(tmp_path: Path) -> None:
    src = _project(tmp_path)
    result = runner.invoke(
        app,
        ["scan", str(src), "--json", "--min-score", "42.1", "--no-auto-cov", "--no-git"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert [fn["qualname"] for fn in payload["functions"]] == ["branchy"]


def test_scan_allow_suppresses_matching_function(tmp_path: Path) -> None:
    src = _project(tmp_path)
    result = runner.invoke(
        app,
        ["scan", str(src), "--json", "--allow", "branchy", "--no-auto-cov", "--no-git"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert [fn["qualname"] for fn in payload["functions"]] == ["trivial"]
    assert payload["summary"]["suppressed_functions"] == 1


def test_scan_allow_suppresses_path_pattern(tmp_path: Path) -> None:
    src = _project(tmp_path)
    result = runner.invoke(
        app,
        ["scan", str(src), "--json", "--allow", "**/m.py", "--no-auto-cov", "--no-git"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["functions"] == []
    assert payload["summary"]["suppressed_functions"] == 2


def test_check_allow_suppresses_new_risky_function(tmp_path: Path) -> None:
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
    (src / "risky.py").write_text(
        dedent(
            """
            def risky(a, b, c, d, e, f, g, h, i, j):
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
                if f:
                    return 6
                if g:
                    return 7
                if h:
                    return 8
                if i:
                    return 9
                if j:
                    return 10
                return 0
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "check",
            str(src),
            "--baseline",
            str(baseline_path),
            "--fail-new-above",
            "10",
            "--allow",
            "risky",
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 0, result.stdout


def test_scan_rejects_invalid_missing_coverage_policy(tmp_path: Path) -> None:
    src = _project(tmp_path)
    result = runner.invoke(
        app,
        ["scan", str(src), "--missing-coverage", "unknown", "--no-auto-cov", "--no-git"],
    )
    assert result.exit_code == 2
    assert "missing coverage policy must be one of" in result.stderr


def test_scan_missing_coverage_skip_drops_unmapped_file(tmp_path: Path) -> None:
    src = _project(tmp_path)
    coverage_path = tmp_path / "coverage.json"
    coverage_path.write_text('{"files": {}}', encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "scan",
            str(src),
            "--coverage",
            str(coverage_path),
            "--missing-coverage",
            "skip",
            "--json",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["functions"] == []
    assert payload["summary"]["skipped_missing_coverage"] == 2


def test_diff_json_reports_unchanged_entries(tmp_path: Path) -> None:
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
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["summary"]["unchanged"] == 2
    assert {entry["status"] for entry in payload["entries"]} == {"unchanged"}


def test_diff_summary_text_reports_status_counts(tmp_path: Path) -> None:
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
            "--summary",
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 0
    assert result.stdout.startswith("diff regressed=0 component_regressed=0 improved=0 new=0")
    assert "unchanged=2" in result.stdout


def test_diff_pr_comment_has_sticky_marker(tmp_path: Path) -> None:
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
            "--format",
            "pr-comment",
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 0
    assert result.stdout.startswith("<!-- riskratchet-report -->")


def test_check_pr_comment_has_sticky_marker(tmp_path: Path) -> None:
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
            "--format",
            "pr-comment",
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 0
    assert result.stdout.startswith("<!-- riskratchet-report -->")
    assert "**Regressions:** 0" in result.stdout


def test_check_pr_comment_renders_full_diff_but_keeps_failure_exit_code(tmp_path: Path) -> None:
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
    (src / "risky.py").write_text(
        "def risky(a, b, c, d, e, f):\n"
        "    if a: return 1\n"
        "    if b: return 2\n"
        "    if c: return 3\n"
        "    if d: return 4\n"
        "    if e: return 5\n"
        "    if f: return 6\n"
        "    return 0\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "check",
            str(src),
            "--baseline",
            str(baseline_path),
            "--fail-new-above",
            "10",
            "--format",
            "pr-comment",
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 1
    assert result.stdout.startswith("<!-- riskratchet-report -->")
    assert "| new |" in result.stdout
    assert "<details><summary>Unchanged functions (2)</summary>" in result.stdout


def test_check_json_flag_produces_regressions_payload(tmp_path: Path) -> None:
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
    assert "regressions" in payload
    assert payload["regressions"] == []


def test_markdown_source_links_use_github_actions_env_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = _project(tmp_path)
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.example")
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/project")
    monkeypatch.setenv("GITHUB_SHA", "deadbeef")
    result = runner.invoke(
        app,
        ["scan", str(src), "--format", "markdown", "--no-auto-cov", "--no-git"],
    )
    assert result.exit_code == 0
    assert "https://github.example/acme/project/blob/deadbeef/" in result.stdout


def test_groups_use_longest_prefix_and_emit_rollups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "src"
    core = src / "core"
    api = core / "api"
    api.mkdir(parents=True)
    (core / "m.py").write_text("def core_fn(): return 1\n", encoding="utf-8")
    (api / "m.py").write_text("def api_fn(): return 1\n", encoding="utf-8")
    config = tmp_path / "pyproject.toml"
    config.write_text(
        dedent(
            """
            [tool.riskratchet]
            paths = ["src"]

            [tool.riskratchet.groups]
            core = "src/core"
            api = "src/core/api"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["scan", "src", "--config", str(config), "--json", "--no-auto-cov", "--no-git"],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    by_qualname = {fn["qualname"]: fn for fn in payload["functions"]}
    assert by_qualname["core_fn"]["group"] == "core"
    assert by_qualname["api_fn"]["group"] == "api"
    assert payload["summary"]["groups"]["core"]["functions"] == 1
    assert payload["summary"]["groups"]["api"]["functions"] == 1


def test_config_validate_success_and_failure_paths(tmp_path: Path) -> None:
    valid = tmp_path / "pyproject.toml"
    valid.write_text(
        dedent(
            """
            [tool.riskratchet]
            churn_window_days = 30

            [tool.riskratchet.groups]
            core = ["src/core", "lib/core"]
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    ok = runner.invoke(app, ["config", "validate", "--config", str(valid)])
    assert ok.exit_code == 0

    invalid = tmp_path / "invalid.toml"
    invalid.write_text("[tool.riskratchet]\nunknown = true\n", encoding="utf-8")
    bad = runner.invoke(app, ["config", "validate", "--config", str(invalid)])
    assert bad.exit_code == 2
    assert "unknown [tool.riskratchet] key" in bad.stderr


def test_config_show_json_includes_resolved_defaults(tmp_path: Path) -> None:
    config = tmp_path / "pyproject.toml"
    config.write_text("[tool.riskratchet]\npaths = ['src']\n", encoding="utf-8")
    result = runner.invoke(app, ["config", "show", "--config", str(config), "--json"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["config_path"] == str(config)
    assert payload["config"]["baseline"] == ".riskratchet.json"
    assert payload["config"]["fail_new_above"] == 50.0


def test_check_baseline_format_riskratchet_behaves_like_default(tmp_path: Path) -> None:
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
            "--baseline-format",
            "riskratchet",
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 0, result.stdout


def test_check_emits_regression_hint_on_stderr(tmp_path: Path) -> None:
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
    # Mutate `branchy` to dramatically higher complexity, forcing a regression
    # past the default +5 score tolerance.
    branches = "\n".join(f"    if x == {n}: return {n}" for n in range(20))
    (src / "m.py").write_text(
        "def trivial():\n    return 1\n\ndef branchy(x):\n" + branches + "\n    return 0\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        [
            "check",
            str(src),
            "--baseline",
            str(baseline_path),
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 1
    # Hint goes to stderr so --json stdout consumers stay clean.
    assert "regressions detected" in result.stderr
    assert "riskratchet baseline" in result.stderr


def test_check_rejects_unsupported_baseline_format(tmp_path: Path) -> None:
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
            "--baseline-format",
            "sarif",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 2
    assert "unsupported baseline format" in result.stderr
