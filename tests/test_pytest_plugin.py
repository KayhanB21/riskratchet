"""Integration tests for the riskratchet pytest plugin.

Use pytester to drive a sub-pytest session and verify that the plugin
flips the exit status to non-zero when a regression is detected, and
leaves it alone otherwise.
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest
from typer.testing import CliRunner

from riskratchet.cli import app

pytest_plugins = ["pytester"]
runner = CliRunner()


def _write(path: Path, source: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(source).strip() + "\n", encoding="utf-8")
    return path


def _baseline_payload(entries: list[dict[str, object]]) -> dict[str, object]:
    return {"version": "1", "entries": entries}


def _entry(path: str, qualname: str, score: float) -> dict[str, object]:
    return {
        "path": path,
        "qualname": qualname,
        "score": score,
        "components": {
            "coverage_gap": score,
            "structural_complexity": score,
            "branch_gap": 0.0,
            "churn": 0.0,
            "public_surface": score,
            "sprawl": 0.0,
        },
    }


def test_plugin_passes_when_no_regressions(pytester: pytest.Pytester) -> None:
    src = pytester.path / "src"
    _write(src / "m.py", "def trivial():\n    return 1\n")
    _write(
        pytester.path / "tests" / "test_m.py",
        "def test_truthy():\n    assert True\n",
    )
    baseline = pytester.path / ".riskratchet.json"
    baseline.write_text(json.dumps(_baseline_payload([])), encoding="utf-8")

    result = pytester.runpytest_subprocess(
        "--cov=src",
        "--cov-report=json:coverage.json",
        "--riskratchet",
        "--riskratchet-paths",
        str(src),
        "--riskratchet-baseline",
        str(baseline),
    )
    assert result.ret == 0, result.stdout.str()


def test_plugin_fails_on_new_risky_function(pytester: pytest.Pytester) -> None:
    src = pytester.path / "src"
    _write(
        src / "risky.py",
        """
        def risky(a, b, c, d, e, f, g, h, i, j):
            if a: return 1
            if b: return 2
            if c: return 3
            if d: return 4
            if e: return 5
            if f: return 6
            if g: return 7
            if h: return 8
            if i: return 9
            if j: return 10
            return 0
        """,
    )
    _write(
        pytester.path / "tests" / "test_m.py",
        "def test_truthy():\n    assert True\n",
    )
    baseline = pytester.path / ".riskratchet.json"
    baseline.write_text(json.dumps(_baseline_payload([])), encoding="utf-8")

    result = pytester.runpytest_subprocess(
        "--cov=src",
        "--cov-report=json:coverage.json",
        "--riskratchet",
        "--riskratchet-paths",
        str(src),
        "--riskratchet-baseline",
        str(baseline),
        "--riskratchet-fail-new-above",
        "10",
    )
    assert result.ret == 1, result.stdout.str()
    assert "riskratchet" in result.stdout.str().lower()


def test_plugin_fails_when_baseline_missing(pytester: pytest.Pytester) -> None:
    src = pytester.path / "src"
    _write(src / "m.py", "def trivial():\n    return 1\n")
    _write(
        pytester.path / "tests" / "test_m.py",
        "def test_truthy():\n    assert True\n",
    )

    result = pytester.runpytest_subprocess(
        "--riskratchet",
        "--riskratchet-paths",
        str(src),
        "--riskratchet-baseline",
        str(pytester.path / "nope.json"),
    )
    assert result.ret == 1, result.stdout.str()
    assert "baseline file not found" in result.stdout.str().lower()


def test_plugin_inactive_when_flag_absent(pytester: pytest.Pytester) -> None:
    """Without --riskratchet the plugin is a no-op even if a baseline is missing."""
    src = pytester.path / "src"
    _write(src / "m.py", "def trivial():\n    return 1\n")
    _write(
        pytester.path / "tests" / "test_m.py",
        "def test_truthy():\n    assert True\n",
    )
    result = pytester.runpytest_subprocess(
        "--riskratchet-paths",
        str(src),
    )
    assert result.ret == 0, result.stdout.str()


# --- 0.3.5: the plugin is the same gate, not a second one --------------------------
#
# The plugin read no `[tool.riskratchet]` at all, so on one project it scanned a
# hardcoded `src` that did not exist, gated at its own +5 where the repo had asked for
# +1, scored with default weights against a baseline written with configured ones, and
# printed raw paths into CI logs for a repo running `private_comment = true`.

_RISKY = """
def risky(a, b, c, d, e, f, g, h, i, j):
    if a: return 1
    if b: return 2
    if c: return 3
    if d: return 4
    if e: return 5
    if f: return 6
    if g: return 7
    if h: return 8
    if i: return 9
    if j: return 10
    return 0
"""

# Everything non-default: a scan root that is not `src`, a threshold tighter than the
# plugin's old hardcoded 5.0, and redaction. Each one alone was enough to make the two
# entry points disagree.
_CONFIG = """
[tool.riskratchet]
paths = ["lib"]
fail_regression_above = 1
private_comment = true
redact_salt = "fixed-for-the-test"
"""


def _collapsed(text: str) -> str:
    """Flatten a Rich table back to prose.

    Long cells wrap, so a phrase straddles a line break with the table's border
    glyphs sitting between its halves. Dropping the borders and collapsing runs of
    whitespace makes the rendered text assertable without pinning column widths.
    """
    return " ".join(text.replace("│", " ").replace("┃", " ").split())


def _configured_project(pytester: pytest.Pytester) -> Path:
    """A project whose config the plugin must obey, with a real regression present."""
    _write(pytester.path / "lib" / "app.py", _RISKY)
    _write(pytester.path / "tests" / "test_app.py", "def test_truthy():\n    assert True\n")
    (pytester.path / "pyproject.toml").write_text(_CONFIG, encoding="utf-8")
    baseline = pytester.path / ".riskratchet.json"
    baseline.write_text(
        json.dumps(_baseline_payload([_entry("lib/app.py", "risky", 10.0)])), encoding="utf-8"
    )
    return baseline


def test_the_plugin_and_the_cli_reach_the_same_verdict(pytester: pytest.Pytester) -> None:
    """The trip-wire. Same project, same baseline, same config — one verdict.

    Deliberately passes no `--riskratchet-paths`: the plugin has to find `lib` from
    config, which is exactly what it could not do before. Asserting the *rendered*
    tolerance and the redacted identity as well as the exit code is what catches a
    plugin that fails for the right reason with the wrong numbers.
    """
    baseline = _configured_project(pytester)

    plugin = pytester.runpytest_subprocess("--cov=lib", "--cov-report=json:coverage.json", "--riskratchet")
    cli = runner.invoke(
        app, ["check", "--baseline", str(baseline), "--no-auto-cov", "--no-git", "--allow-missing-coverage"]
    )

    assert plugin.ret == 1, plugin.stdout.str()
    assert cli.exit_code == 1, cli.output
    # Rich wraps table cells, so compare on collapsed whitespace rather than raw text.
    plugin_text = _collapsed(plugin.stdout.str())
    # The configured tolerance, not the plugin's old hardcoded default.
    assert "tolerance is +1.0" in plugin_text
    assert "tolerance is +1.0" in _collapsed(cli.output)
    # Redaction was asked for in config, so neither surface may print the real name.
    assert "risky" not in plugin_text
    assert "lib/app.py" not in plugin_text


def test_the_plugin_reads_scan_paths_from_config(pytester: pytest.Pytester) -> None:
    """`paths = ["lib"]` while the plugin defaulted to `src` meant zero functions.

    Zero functions produced no regressions and a green session — 0.3.4's empty-scan
    fix was still reachable around, because it only ever ran in the CLI.
    """
    _configured_project(pytester)

    result = pytester.runpytest_subprocess("--cov=lib", "--cov-report=json:coverage.json", "--riskratchet")

    # It had to find `lib` from config to see the function at all; scanning the old
    # hardcoded `src` would have found nothing and passed.
    assert result.ret == 1, result.stdout.str()
    assert "regressions detected" in _collapsed(result.stdout.str())
    assert "gating nothing" not in _collapsed(result.stdout.str())


def test_a_scan_that_finds_nothing_fails_the_session(pytester: pytest.Pytester) -> None:
    """The plugin's copy of 0.3.4's guard: gating nothing is not passing."""
    _write(pytester.path / "lib" / "app.py", _RISKY)
    _write(pytester.path / "tests" / "test_app.py", "def test_truthy():\n    assert True\n")
    (pytester.path / "pyproject.toml").write_text(
        '[tool.riskratchet]\npaths = ["nowhere"]\n', encoding="utf-8"
    )
    baseline = pytester.path / ".riskratchet.json"
    baseline.write_text(
        json.dumps(_baseline_payload([_entry("lib/app.py", "risky", 10.0)])), encoding="utf-8"
    )

    result = pytester.runpytest_subprocess("--cov=lib", "--cov-report=json:coverage.json", "--riskratchet")

    assert result.ret == 1, result.stdout.str()
    assert "gating nothing" in result.stdout.str()


def test_an_unusable_config_value_fails_the_session(pytester: pytest.Pytester) -> None:
    """0.3.4 made this exit 2 in the CLI; the plugin kept applying the default."""
    _write(pytester.path / "lib" / "app.py", _RISKY)
    _write(pytester.path / "tests" / "test_app.py", "def test_truthy():\n    assert True\n")
    (pytester.path / "pyproject.toml").write_text(
        '[tool.riskratchet]\npaths = ["lib"]\nfail_regression_above = "1"\n', encoding="utf-8"
    )
    (pytester.path / ".riskratchet.json").write_text(json.dumps(_baseline_payload([])), encoding="utf-8")

    result = pytester.runpytest_subprocess("--cov=lib", "--cov-report=json:coverage.json", "--riskratchet")

    assert result.ret == 1, result.stdout.str()
    assert "invalid config" in result.stdout.str()
