"""Pytest plugin that runs `riskratchet check` after the test session.

Activate with `--riskratchet`. The plugin is a second front door to the same
gate the CLI runs, and since 0.3.5 it goes through the same resolution: it reads
`[tool.riskratchet]` via `config.resolve_gate_settings`, so scan paths,
thresholds, weights, `include`/`exclude`/`allow`, churn window, and the
missing-coverage policy all come from the project's own config, with any
`--riskratchet-*` option overriding. It also applies the guards the CLI grew
between 0.3.2 and 0.3.4 — config validation, the zero-function check, the
shallow-clone warning — and redacts its output when the project asked for it.

It did none of that before 0.3.5. Dating from 0.2.0, it had drifted twenty
releases behind: it scored with default weights against a baseline written with
configured ones, scanned a hardcoded `src`, used its own thresholds, and printed
raw paths and qualnames into CI logs for repos running `private_comment = true`.
`tests/test_pytest_plugin.py::test_the_plugin_and_the_cli_reach_the_same_verdict`
is what keeps the two in step from here.

The plugin reads a coverage JSON file written during the run, so the user
must already be collecting coverage in a format compatible with
`coverage.py` (e.g. `pytest --cov --cov-report=json:coverage.json`).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pytest

    from riskratchet.config import GateSettings
    from riskratchet.models import Baseline, Regression, RiskReport


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
        help="Paths to scan. Defaults to [tool.riskratchet] paths, else ['src']. Repeatable.",
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
        default=None,
        help="Score above which a new function fails. Defaults to [tool.riskratchet], else 50.",
    )
    group.addoption(
        "--riskratchet-fail-regression-above",
        action="store",
        type=float,
        default=None,
        help="Score delta failing the session. Defaults to [tool.riskratchet], else 5.",
    )
    group.addoption(
        "--riskratchet-fail-existing-above",
        action="store",
        type=float,
        default=None,
        help="Current score above which existing debt fails the session. Default unset.",
    )
    group.addoption(
        "--riskratchet-fail-component-regression-above",
        action="store",
        type=float,
        default=None,
        help="Component score delta failing the session. Defaults to [tool.riskratchet], else 15.",
    )
    group.addoption(
        "--riskratchet-no-component-regression-gate",
        action="store_true",
        default=False,
        help="Disable per-component regression checks.",
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    config = session.config
    if not config.getoption("--riskratchet"):
        return
    if exitstatus not in (0, 1):
        return

    # Imported lazily so that enabling the plugin entry point does not pull the
    # whole package in before pytest-cov has a chance to start coverage. Without
    # this, all module-level lines in riskratchet/* show as "missing".
    from riskratchet.baseline import compare
    from riskratchet.engine import analyze
    from riskratchet.git import is_shallow_repo

    rootdir = Path(str(config.rootpath))
    baseline_path = _resolve(rootdir, config.getoption("--riskratchet-baseline"))
    coverage_path = _resolve(rootdir, config.getoption("--riskratchet-coverage"))
    resolved = _settings_or_fail(session, rootdir)
    if resolved is None:
        return
    cfg, config_dir, settings = resolved

    if not baseline_path.exists():
        _emit(
            session,
            f"riskratchet: baseline file not found: {baseline_path}. Run `riskratchet baseline` first.",
        )
        session.exitstatus = 1
        return
    if not coverage_path.exists():
        _emit(
            session,
            f"riskratchet: coverage file not found: {coverage_path}. "
            "Run pytest with `--cov --cov-report=json:coverage.json`.",
        )
        session.exitstatus = 1
        return

    baseline = _loaded_baseline(session, baseline_path)
    if baseline is None:
        return

    if is_shallow_repo(config_dir):
        # Same notice `cli._build_report_or_exit` gives: on a depth-1 CI clone
        # `git log --since` sees only HEAD, so churn scores zero and the session
        # silently disagrees with a locally-generated baseline.
        _emit(
            session,
            "riskratchet: shallow clone detected; churn signals score as zero. "
            "Use actions/checkout with 'fetch-depth: 0' (or run 'git fetch --unshallow').",
        )

    report = analyze(
        settings.paths,
        root=config_dir,
        coverage_path=coverage_path,
        include=settings.include,
        exclude=settings.exclude,
        allow=settings.allow,
        use_git=True,
        churn_days=settings.churn_days,
        weights=settings.weights,
        missing_coverage_policy=settings.missing_coverage,
        groups=settings.groups,
    )
    if _refuses_to_gate_nothing(session, report, baseline_entries=len(baseline.entries)):
        return

    regressions = compare(
        report,
        baseline,
        fail_new_above=settings.fail_new_above,
        fail_regression_above=settings.fail_regression_above,
        fail_existing_above=settings.fail_existing_above,
        fail_component_regression_above=settings.fail_component_regression_above,
        component_regression_gate=settings.component_regression_gate,
    )
    if not regressions:
        return

    _report_regressions(session, regressions, cfg=cfg, config_dir=config_dir)
    session.exitstatus = 1


def _settings_or_fail(
    session: pytest.Session, rootdir: Path
) -> tuple[dict[str, Any], Path, GateSettings] | None:
    """Read the project's config and resolve it, or fail the session explaining why.

    `None` means "already reported, stop here". A value this build cannot use must not
    become a silent default here any more than in the CLI, where 0.3.4 made it exit 2.
    """
    from riskratchet.config import (
        discover_config,
        invalid_config_values,
        resolve_gate_settings,
        unknown_config_keys,
    )

    config = session.config
    cfg, config_dir = discover_config(None)
    problems = invalid_config_values(cfg)
    if problems:
        for problem in problems:
            _emit(session, f"riskratchet: invalid config: {problem}")
        session.exitstatus = 1
        return None
    for key in unknown_config_keys(cfg):
        _emit(session, f"riskratchet: unknown [tool.riskratchet] key: {key}")

    paths_opt: list[str] | None = config.getoption("--riskratchet-paths")
    settings = resolve_gate_settings(
        cfg,
        config_dir,
        paths=[_resolve(rootdir, p) for p in paths_opt] if paths_opt else None,
        fail_new_above=config.getoption("--riskratchet-fail-new-above"),
        fail_regression_above=config.getoption("--riskratchet-fail-regression-above"),
        fail_existing_above=config.getoption("--riskratchet-fail-existing-above"),
        fail_component_regression_above=config.getoption("--riskratchet-fail-component-regression-above"),
        component_regression_gate=not bool(config.getoption("--riskratchet-no-component-regression-gate")),
    )
    return cfg, config_dir, settings


def _report_regressions(
    session: pytest.Session,
    regressions: list[Regression],
    *,
    cfg: dict[str, Any],
    config_dir: Path,
) -> None:
    """Render the regressions table, redacted if the project asked for that.

    Redaction is an output transform applied after the gate, so it can never change the
    verdict — but it does have to happen. `redact_regressions` exists for exactly this
    table, and the plugin used to print raw paths and qualnames straight into CI logs
    for repos running `redact_paths` / `private_comment`.
    """
    from riskratchet.config import resolve_redaction
    from riskratchet.redaction import redact_regressions
    from riskratchet.reporting import render_regressions_table

    redaction = resolve_redaction(
        redact_paths=False,
        redact_qualnames=False,
        private_comment=False,
        redact_salt=None,
        cfg=cfg,
        config_dir=config_dir,
        warn=lambda message: _emit(session, message),
    )
    _emit(session, "riskratchet: regressions detected")
    _emit(session, render_regressions_table(redact_regressions(regressions, redaction)))


def _refuses_to_gate_nothing(session: pytest.Session, report: RiskReport, *, baseline_entries: int) -> bool:
    """Fail the session when the scan found nothing but the baseline holds entries.

    0.3.4 closed this for `riskratchet check` and not for the plugin, so the fix was
    still reachable around: a project scanning `lib` while the plugin defaulted to a
    non-existent `src` produced zero functions, no regressions, and a green session.
    Zero functions against an *empty* baseline is a legitimately empty project.
    """
    if report.functions or not baseline_entries:
        return False
    cause = (
        f"{report.analyzed_functions} function(s) found, all suppressed by an allow pattern"
        if report.analyzed_functions
        else "no functions were found to analyze"
    )
    _emit(
        session,
        f"riskratchet: {cause}, but the baseline has {baseline_entries} entr"
        f"{'y' if baseline_entries == 1 else 'ies'} — the ratchet would be gating nothing. "
        "Check --riskratchet-paths and [tool.riskratchet] paths / include / exclude / allow.",
    )
    session.exitstatus = 1
    return True


def _loaded_baseline(session: pytest.Session, path: Path) -> Baseline | None:
    """Read the baseline, or fail the session explaining why it could not be read.

    `None` means "already reported, stop here". A baseline this build cannot
    parse must not fall through to a comparison — an empty baseline passes every
    gate, so a silent pass is the one outcome worse than a failed session.

    The import stays lazy for the same reason as its caller's: the `pytest11`
    entry point loads this module before pytest-cov starts its tracer.
    """
    from riskratchet.baseline import load_baseline

    try:
        return load_baseline(path)
    except ValueError as exc:
        _emit(session, f"riskratchet: cannot read baseline {path}: {exc}")
        session.exitstatus = 1
        return None


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
