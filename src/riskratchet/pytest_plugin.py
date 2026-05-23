"""Pytest plugin that runs `riskratchet check` after the test session.

Activate with `--riskratchet`. The plugin runs the same `analyze` plus
`compare` core the CLI uses, so behaviour stays consistent across entry
points.

The plugin reads a coverage JSON file written during the run, so the user
must already be collecting coverage in a format compatible with
`coverage.py` (e.g. `pytest --cov --cov-report=json:coverage.json`).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from riskratchet.baseline import compare, load_baseline
from riskratchet.engine import analyze
from riskratchet.reporting import render_regressions_table

if TYPE_CHECKING:
    import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("riskratchet", "maintainability ratchet")
    group.addoption(
        "--riskratchet",
        action="store_true",
        default=False,
        help="Run riskratchet check after the test session and fail on regressions.",
    )
    group.addoption(
        "--riskratchet-paths",
        action="append",
        default=None,
        help="Paths to scan. Defaults to ['src']. Repeat for multiple paths.",
    )
    group.addoption(
        "--riskratchet-baseline",
        action="store",
        default=".riskratchet.json",
        help="Path to the baseline JSON. Defaults to .riskratchet.json.",
    )
    group.addoption(
        "--riskratchet-coverage",
        action="store",
        default="coverage.json",
        help="Path to the coverage JSON. Defaults to coverage.json.",
    )
    group.addoption(
        "--riskratchet-fail-new-above",
        action="store",
        type=float,
        default=50.0,
        help="Score above which a new function fails the session. Default 50.",
    )
    group.addoption(
        "--riskratchet-fail-regression-above",
        action="store",
        type=float,
        default=5.0,
        help="Score delta above which a regression fails the session. Default 5.",
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    config = session.config
    if not config.getoption("--riskratchet"):
        return
    if exitstatus not in (0, 1):
        return

    rootdir = Path(str(config.rootpath))
    baseline_path = _resolve(rootdir, config.getoption("--riskratchet-baseline"))
    coverage_path = _resolve(rootdir, config.getoption("--riskratchet-coverage"))
    paths_opt: list[str] | None = config.getoption("--riskratchet-paths")
    paths = [_resolve(rootdir, p) for p in (paths_opt or ["src"])]

    if not baseline_path.exists():
        _emit(
            session,
            f"riskratchet: baseline file not found: {baseline_path}. Run `riskratchet baseline` first.",
        )
        session.exitstatus = 1
        return

    report = analyze(
        paths,
        root=rootdir,
        coverage_path=coverage_path if coverage_path.exists() else None,
        use_git=True,
    )
    regressions = compare(
        report,
        load_baseline(baseline_path),
        fail_new_above=float(config.getoption("--riskratchet-fail-new-above")),
        fail_regression_above=float(config.getoption("--riskratchet-fail-regression-above")),
    )
    if not regressions:
        return

    _emit(session, "riskratchet: regressions detected")
    _emit(session, render_regressions_table(regressions))
    session.exitstatus = 1


def _resolve(rootdir: Path, value: object) -> Path:
    text = str(value)
    candidate = Path(text)
    return candidate if candidate.is_absolute() else (rootdir / candidate)


def _emit(session: pytest.Session, message: str) -> None:
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(message)
    else:
        print(message)
