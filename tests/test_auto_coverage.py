"""Tests for the auto-coverage shim.

`ensure_coverage` is the gate between "the user gave us a coverage file" and
"we need to shell out to pytest to produce one". These tests pin the
precedence rules (explicit > cache > regenerate) and the cache freshness
check.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path

from riskratchet.auto_coverage import (
    DEFAULT_TEST_COMMAND,
    AutoCoverageResult,
    ensure_coverage,
)

_FAKE_COVERAGE = {"files": {"src/m.py": {"executed_lines": [1], "missing_lines": []}}}


def _writer_runner(path: Path) -> Callable[[str], int]:
    def run(command: str) -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_FAKE_COVERAGE), encoding="utf-8")
        return 0

    return run


def _failing_runner(returncode: int = 1) -> Callable[[str], int]:
    def run(command: str) -> int:
        return returncode

    return run


def _silent_log(_message: str) -> None:
    return None


def test_explicit_path_wins_even_when_cache_exists(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit.json"
    explicit.write_text(json.dumps(_FAKE_COVERAGE), encoding="utf-8")
    cache = tmp_path / ".riskratchet" / "coverage.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps(_FAKE_COVERAGE), encoding="utf-8")

    result = ensure_coverage(
        requested=explicit,
        sources=[tmp_path / "src"],
        cache_path=cache,
        test_command=DEFAULT_TEST_COMMAND,
        enabled=True,
        runner=_failing_runner(),
        log=_silent_log,
    )

    assert result == AutoCoverageResult(path=explicit, source="explicit")


def test_disabled_returns_none_without_running_tests(tmp_path: Path) -> None:
    cache = tmp_path / ".riskratchet" / "coverage.json"
    runs: list[str] = []

    def runner(command: str) -> int:
        runs.append(command)
        return 0

    result = ensure_coverage(
        requested=None,
        sources=[tmp_path / "src"],
        cache_path=cache,
        test_command=DEFAULT_TEST_COMMAND,
        enabled=False,
        runner=runner,
        log=_silent_log,
    )

    assert result.path is None
    assert result.source == "disabled"
    assert runs == []
    assert not cache.exists()


def test_fresh_cache_is_reused(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text("def f(): return 1\n")
    cache = tmp_path / ".riskratchet" / "coverage.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps(_FAKE_COVERAGE), encoding="utf-8")
    # Cache newer than source.
    future = time.time() + 10
    import os

    os.utime(cache, (future, future))
    runs: list[str] = []

    def runner(command: str) -> int:
        runs.append(command)
        return 0

    result = ensure_coverage(
        requested=None,
        sources=[src],
        cache_path=cache,
        test_command=DEFAULT_TEST_COMMAND,
        enabled=True,
        runner=runner,
        log=_silent_log,
    )

    assert result.path == cache
    assert result.source == "cache"
    assert runs == []


def test_stale_cache_triggers_regeneration(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    py = src / "m.py"
    py.write_text("def f(): return 1\n")
    cache = tmp_path / ".riskratchet" / "coverage.json"
    cache.parent.mkdir(parents=True)
    cache.write_text("stale", encoding="utf-8")
    # Source newer than cache.
    future = time.time() + 10
    import os

    os.utime(py, (future, future))

    result = ensure_coverage(
        requested=None,
        sources=[src],
        cache_path=cache,
        test_command="echo {output}",
        enabled=True,
        runner=_writer_runner(cache),
        log=_silent_log,
    )

    assert result.path == cache
    assert result.source == "generated"
    assert result.returncode == 0
    assert json.loads(cache.read_text(encoding="utf-8")) == _FAKE_COVERAGE


def test_missing_cache_triggers_regeneration(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text("def f(): return 1\n")
    cache = tmp_path / ".riskratchet" / "coverage.json"

    result = ensure_coverage(
        requested=None,
        sources=[src],
        cache_path=cache,
        test_command="ignored",
        enabled=True,
        runner=_writer_runner(cache),
        log=_silent_log,
    )

    assert result.path == cache
    assert result.source == "generated"
    assert cache.exists()


def test_runner_failure_without_output_returns_none(tmp_path: Path) -> None:
    cache = tmp_path / ".riskratchet" / "coverage.json"
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text("def f(): return 1\n")

    result = ensure_coverage(
        requested=None,
        sources=[src],
        cache_path=cache,
        test_command="ignored",
        enabled=True,
        runner=_failing_runner(returncode=5),
        log=_silent_log,
    )

    assert result.path is None
    assert result.source == "generated_missing"
    assert result.returncode == 5
    assert not cache.exists()


def test_runner_failure_with_partial_output_is_still_used(tmp_path: Path) -> None:
    """If pytest exits non-zero but still wrote coverage, we use it.

    pytest exits non-zero when tests fail, but coverage data is still written.
    The risk signal is still better-than-nothing in that case.
    """
    cache = tmp_path / ".riskratchet" / "coverage.json"
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text("def f(): return 1\n")

    def runner(command: str) -> int:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(_FAKE_COVERAGE), encoding="utf-8")
        return 1  # tests failed

    result = ensure_coverage(
        requested=None,
        sources=[src],
        cache_path=cache,
        test_command="ignored",
        enabled=True,
        runner=runner,
        log=_silent_log,
    )

    assert result.path == cache
    assert result.source == "generated"
    assert result.returncode == 1


def test_scan_cli_uses_test_command_to_generate_coverage(tmp_path: Path) -> None:
    """End-to-end: scan with no coverage triggers the configured test command.

    We point `test_command` at a tiny shell script that writes a coverage JSON
    file at `{output}`. The CLI should call it, find the cache, and report.
    """
    from typer.testing import CliRunner

    import riskratchet.auto_coverage as auto_coverage
    from riskratchet.cli import app

    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text("def f(): return 1\n", encoding="utf-8")
    cache = tmp_path / ".riskratchet" / "coverage.json"
    config = tmp_path / "pyproject.toml"
    config.write_text(
        f"[tool.riskratchet]\n"
        f"auto_coverage = true\n"
        f'coverage_cache = "{cache.as_posix()}"\n'
        f'test_command = "stub-runner {{output}}"\n',
        encoding="utf-8",
    )

    received: list[str] = []

    def stub_runner(command: str) -> int:
        received.append(command)
        # Simulate the test command writing a coverage.json file.
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(_FAKE_COVERAGE), encoding="utf-8")
        return 0

    # Override the conftest-blocked default runner just for this test.
    original = auto_coverage._default_runner
    auto_coverage._default_runner = stub_runner
    try:
        result = CliRunner().invoke(
            app,
            ["scan", str(src), "--config", str(config), "--json", "--no-git"],
        )
    finally:
        auto_coverage._default_runner = original

    assert result.exit_code == 0, result.stdout
    assert received == [f"stub-runner {cache}"]
    payload = json.loads(result.stdout)
    assert "functions" in payload


def test_explicit_path_missing_falls_through_to_cache(tmp_path: Path) -> None:
    """If --coverage X was passed but X doesn't exist, we still try to help."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text("def f(): return 1\n")
    cache = tmp_path / ".riskratchet" / "coverage.json"
    missing_explicit = tmp_path / "does-not-exist.json"

    result = ensure_coverage(
        requested=missing_explicit,
        sources=[src],
        cache_path=cache,
        test_command="ignored",
        enabled=True,
        runner=_writer_runner(cache),
        log=_silent_log,
    )

    assert result.path == cache
    assert result.source == "generated"
