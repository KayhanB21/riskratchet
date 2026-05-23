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

pytest_plugins = ["pytester"]


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
