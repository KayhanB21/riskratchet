"""Direct unit tests for riskratchet.pytest_plugin internals.

The subprocess-based integration tests in test_pytest_plugin.py cover the
end-to-end behaviour; this module pokes at the same functions in-process
so coverage reflects the lines that run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from riskratchet.pytest_plugin import _emit, _resolve, pytest_sessionfinish


class _StubReporter:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write_line(self, message: str) -> None:
        self.lines.append(message)


class _StubPluginManager:
    def __init__(self, reporter: _StubReporter | None) -> None:
        self._reporter = reporter

    def get_plugin(self, name: str) -> _StubReporter | None:
        if name == "terminalreporter":
            return self._reporter
        return None


class _StubConfig:
    def __init__(self, rootpath: Path, options: dict[str, Any], reporter: _StubReporter | None) -> None:
        self.rootpath = rootpath
        self.pluginmanager = _StubPluginManager(reporter)
        self._options = options

    def getoption(self, name: str) -> Any:
        return self._options.get(name)


class _StubSession:
    def __init__(self, config: _StubConfig) -> None:
        self.config = config
        self.exitstatus = 0


def _make_session(
    rootpath: Path,
    *,
    enabled: bool = True,
    baseline: str = ".riskratchet.json",
    coverage: str = "coverage.json",
    paths: list[str] | None = None,
    fail_new_above: float = 50.0,
    fail_regression_above: float = 5.0,
    reporter: _StubReporter | None = None,
) -> _StubSession:
    options = {
        "--riskratchet": enabled,
        "--riskratchet-baseline": baseline,
        "--riskratchet-coverage": coverage,
        "--riskratchet-paths": paths,
        "--riskratchet-fail-new-above": fail_new_above,
        "--riskratchet-fail-regression-above": fail_regression_above,
    }
    return _StubSession(_StubConfig(rootpath, options, reporter))


def test_resolve_absolute_path_stays_absolute(tmp_path: Path) -> None:
    abs_path = tmp_path / "x.json"
    assert _resolve(tmp_path, abs_path) == abs_path


def test_resolve_relative_path_anchors_to_root(tmp_path: Path) -> None:
    assert _resolve(tmp_path, "x.json") == tmp_path / "x.json"


def test_emit_prefers_terminal_reporter_when_available() -> None:
    reporter = _StubReporter()
    session = _make_session(Path("."), reporter=reporter)
    _emit(session, "hello")  # type: ignore[arg-type]
    assert reporter.lines == ["hello"]


def test_emit_falls_back_to_print_when_no_reporter(capsys: pytest.CaptureFixture[str]) -> None:
    session = _make_session(Path("."), reporter=None)
    _emit(session, "fallback")  # type: ignore[arg-type]
    assert "fallback" in capsys.readouterr().out


def test_sessionfinish_is_noop_when_flag_absent(tmp_path: Path) -> None:
    session = _make_session(tmp_path, enabled=False)
    pytest_sessionfinish(session, 0)  # type: ignore[arg-type]
    assert session.exitstatus == 0


def test_sessionfinish_is_noop_when_tests_errored_hard(tmp_path: Path) -> None:
    session = _make_session(tmp_path, enabled=True)
    pytest_sessionfinish(session, 2)  # type: ignore[arg-type]
    assert session.exitstatus == 0


def test_sessionfinish_marks_failure_when_baseline_missing(tmp_path: Path) -> None:
    reporter = _StubReporter()
    session = _make_session(tmp_path, baseline="absent.json", reporter=reporter)
    pytest_sessionfinish(session, 0)  # type: ignore[arg-type]
    assert session.exitstatus == 1
    assert any("baseline file not found" in line for line in reporter.lines)


def test_sessionfinish_passes_when_no_regressions(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("def trivial():\n    return 1\n", encoding="utf-8")
    (tmp_path / ".riskratchet.json").write_text(
        json.dumps({"version": "1", "entries": []}),
        encoding="utf-8",
    )
    reporter = _StubReporter()
    session = _make_session(tmp_path, paths=["src"], reporter=reporter)
    pytest_sessionfinish(session, 0)  # type: ignore[arg-type]
    assert session.exitstatus == 0
    assert all("regressions detected" not in line for line in reporter.lines)


def test_sessionfinish_marks_failure_on_regression(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "risky.py").write_text(
        "def risky(a,b,c,d,e):\n"
        "    if a: return 1\n"
        "    if b: return 2\n"
        "    if c: return 3\n"
        "    if d: return 4\n"
        "    if e: return 5\n"
        "    return 0\n",
        encoding="utf-8",
    )
    (tmp_path / ".riskratchet.json").write_text(
        json.dumps({"version": "1", "entries": []}),
        encoding="utf-8",
    )
    reporter = _StubReporter()
    session = _make_session(
        tmp_path,
        paths=["src"],
        fail_new_above=5.0,
        reporter=reporter,
    )
    pytest_sessionfinish(session, 0)  # type: ignore[arg-type]
    assert session.exitstatus == 1
    assert any("regressions detected" in line for line in reporter.lines)
