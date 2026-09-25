"""Pytest plugin that runs `riskratchet check` after the test session.

Activate with `--riskratchet`. The plugin is a second front door to the same
gate the CLI runs, and since 0.3.5 it goes through the same resolution: it reads
`[tool.riskratchet]` via `config.resolve_gate_settings`, so scan paths,
thresholds, weights, `include`/`exclude`/`allow`, churn window, and the
missing-coverage policy all come from the project's own config, with any
`--riskratchet-*` option overriding. It also applies the guards the CLI grew
between 0.3.2 and 0.3.4 — config validation, the zero-function check, the
shallow-clone warning — and redacts its output when the project asked for it.

Since 0.3.8 "its output" includes the per-file warnings raised during analysis,
not only the regressions table. Those warnings named real modules on stderr no
matter what `redact_paths` said, because redaction was resolved after the report
was already built. Setup errors — a missing baseline, an unreadable coverage
file — deliberately stay raw: they name a file you must go fix.

It did none of that before 0.3.5. Dating from 0.2.0, it had drifted twenty
releases behind: it scored with default weights against a baseline written with
configured ones, scanned a hardcoded `src`, used its own thresholds, and printed
raw paths and qualnames into CI logs for repos running `private_comment = true`.
`tests/test_pytest_plugin.py::test_the_plugin_and_the_cli_reach_the_same_verdict`
is what keeps the two in step from here.

Since 0.3.6 it builds its report through `pipeline.build_report`, the one seam
every command shares, so `typescript = true` (or `--riskratchet-typescript`)
scores TypeScript here exactly as `riskratchet check` does — with the same
missing-report rule, the same grammar-bump identity guard, and the same warning
when the baseline holds a language the run did not analyze. Until then it called
`engine.analyze` directly, which has no TypeScript path at all, so a mixed
baseline was gated on its Python half only.

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
    from riskratchet.redaction import RedactionConfig


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
        default=None,
        help="Path to the baseline JSON. Defaults to [tool.riskratchet] baseline, else .riskratchet.json.",
    )
    group.addoption(
        "--riskratchet-coverage",
        action="store",
        default=None,
        help="Path to the coverage JSON. Defaults to [tool.riskratchet] coverage, else coverage.json.",
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
    _add_typescript_options(group)
    _add_off_switches(group)


def _add_off_switches(group: pytest.OptionGroup) -> None:
    """The four settings config can turn on, each with a flag that turns it back off (0.3.7).

    The plugin exposes no positive form for these — they are config-only settings — but
    AGENTS.md's invariant is about what *config* can turn on, and this door reads the
    same `[tool.riskratchet]` the CLI does. Leaving them out would mean a repo that sets
    `redact_paths = true` can unredact a `riskratchet check` run and not a `pytest
    --riskratchet` one, which is exactly the kind of door-to-door disagreement 0.3.5 was
    spent removing.
    """
    for flag, setting in (
        ("--riskratchet-no-redact-paths", "redact_paths"),
        ("--riskratchet-no-redact-qualnames", "redact_qualnames"),
        ("--riskratchet-no-private-comment", "private_comment"),
        ("--riskratchet-no-allow-missing-coverage", "allow_missing_coverage"),
    ):
        group.addoption(
            flag,
            action="store_true",
            default=False,
            help=f"Turn off [tool.riskratchet] {setting} for this run.",
        )


def _add_typescript_options(group: pytest.OptionGroup) -> None:
    """The TypeScript options (0.3.6): the switch as two flags, and the two repeatable lists.

    Two `store_true` flags rather than one real-valued default, so that neither can
    shadow `[tool.riskratchet] typescript`: both off means config decides.
    """
    group.addoption(
        "--riskratchet-typescript",
        action="store_true",
        default=False,
        help=(
            "Also analyze and gate TypeScript. Defaults to [tool.riskratchet] typescript, else off. "
            "Needs `pip install 'riskratchet[typescript]'`."
        ),
    )
    group.addoption(
        "--riskratchet-no-typescript",
        action="store_true",
        default=False,
        help="Skip TypeScript even when [tool.riskratchet] sets typescript = true.",
    )
    group.addoption(
        "--riskratchet-ts-coverage",
        action="append",
        default=None,
        help=(
            "Istanbul/LCOV coverage report for TypeScript. Defaults to [tool.riskratchet] ts_coverage. "
            "Repeatable."
        ),
    )
    group.addoption(
        "--riskratchet-ts-entry",
        action="append",
        default=None,
        help=(
            "Entry file that narrows the TypeScript public surface. Defaults to [tool.riskratchet] "
            "ts_entry. Repeatable."
        ),
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
    from riskratchet.git import churn_root_mismatch, is_shallow_repo

    rootdir = Path(str(config.rootpath))
    resolved = _settings_or_fail(session, rootdir)
    if resolved is None:
        return
    cfg, config_dir, settings = resolved
    # Resolved *after* config discovery, and by the same helper the CLI uses: before 0.3.8
    # both came from this plugin's own `default=` literals two lines above this call, so
    # `[tool.riskratchet] baseline` / `coverage` could never be read at all.
    baseline_path = settings.baseline
    coverage_path = settings.coverage
    # Resolved here, before the report exists, because `_report_or_fail` hands this to the
    # warning emitter. `_report_regressions` used to resolve its own copy at the very end of
    # the session, so every per-file warning this plugin printed was raw -- the leak
    # README's 0.3.5 note claimed was already fixed at this door.
    redaction = _resolved_redaction(session, cfg, config_dir)

    if not baseline_path.exists():
        _emit(
            session,
            f"riskratchet: baseline file not found: {baseline_path}. Write one with:",
        )
        _emit(session, f"  {_rebaseline_command(settings, baseline_path, settings.ts_coverage)}")
        session.exitstatus = 1
        return
    if not coverage_path.exists():
        _emit(
            session,
            f"riskratchet: coverage file not found: {coverage_path}. "
            f"Run pytest with `--cov --cov-branch --cov-report=json:{coverage_path}`.",
        )
        session.exitstatus = 1
        return

    baseline = _loaded_baseline(session, baseline_path)
    if baseline is None:
        return
    ts_coverage = _typescript_reports_or_fail(session, settings)
    if ts_coverage is None:
        return
    if not _typescript_entries_ok(session, settings):
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

    repo_root = churn_root_mismatch(config_dir)
    if repo_root is not None:
        # Same notice `cli._warn_churn_root_mismatch` gives. Churn is anchored at the config
        # directory, so a package below the repository root scores 0 churn for every function
        # — silently, and indistinguishably from code nobody has touched.
        _emit(
            session,
            f"riskratchet: churn is scoring 0 for every function — the git repository root is "
            f"{repo_root}, but riskratchet is anchored at {config_dir}, where there is no "
            "history to read. 0.4.0 will score churn from the repository root.",
        )

    report = _report_or_fail(
        session,
        settings,
        config_dir=config_dir,
        coverage_path=coverage_path,
        ts_coverage=ts_coverage,
        redaction=redaction,
    )
    if report is None:
        return
    if _refuses_to_gate_nothing(session, report, baseline_entries=len(baseline.entries)):
        return
    baseline, report = _guarded_typescript_identity(
        session, baseline, report, settings=settings, baseline_path=baseline_path, ts_coverage=ts_coverage
    )

    regressions = _gated(session, report, baseline, settings=settings, config_dir=config_dir)
    if not regressions:
        return

    _report_regressions(session, regressions, redaction=redaction)
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

    settings = resolve_gate_settings(
        cfg,
        config_dir,
        paths=_resolved_all(rootdir, config.getoption("--riskratchet-paths")),
        baseline=_resolved_one(rootdir, config.getoption("--riskratchet-baseline")),
        coverage=_resolved_one(rootdir, config.getoption("--riskratchet-coverage")),
        fail_new_above=config.getoption("--riskratchet-fail-new-above"),
        fail_regression_above=config.getoption("--riskratchet-fail-regression-above"),
        fail_existing_above=config.getoption("--riskratchet-fail-existing-above"),
        fail_component_regression_above=config.getoption("--riskratchet-fail-component-regression-above"),
        component_regression_gate=not bool(config.getoption("--riskratchet-no-component-regression-gate")),
        typescript=_typescript_override(config),
        ts_coverage=_resolved_all(rootdir, config.getoption("--riskratchet-ts-coverage")),
        ts_entry=_resolved_all(rootdir, config.getoption("--riskratchet-ts-entry")),
        no_allow_missing_coverage=bool(config.getoption("--riskratchet-no-allow-missing-coverage")),
    )
    return cfg, config_dir, settings


def _typescript_override(config: pytest.Config) -> bool | None:
    """`--riskratchet-no-typescript` beats `--riskratchet-typescript` beats config.

    `None` lets `[tool.riskratchet] typescript` decide: a switch config can turn on must
    be one a flag can turn back off, so the off-switch is explicit rather than a
    real-valued default that could never lose to config.
    """
    if config.getoption("--riskratchet-no-typescript"):
        return False
    if config.getoption("--riskratchet-typescript"):
        return True
    return None


def _typescript_reports_or_fail(session: pytest.Session, settings: GateSettings) -> list[Path] | None:
    """The TypeScript reports this run may read, or `None` after failing the session.

    The CLI's rule for a gate command: a report that is not on disk is fatal unless
    `allow_missing_coverage`, and only the usable subset goes to the strict loader.
    With TypeScript off the lists are inert, so they are dropped rather than checked —
    a `--riskratchet-no-typescript` run must not fail on a report it will never read.
    """
    from riskratchet.config import usable_ts_coverage

    if not settings.typescript:
        if settings.ts_coverage or settings.ts_entry:
            _emit(
                session,
                "riskratchet: --riskratchet-ts-coverage / --riskratchet-ts-entry have no effect without "
                "--riskratchet-typescript (or `typescript = true` in [tool.riskratchet]); the same goes "
                "for the ts_coverage / ts_entry keys.",
            )
        return []
    allow_missing = settings.allow_missing_coverage
    present, problems = usable_ts_coverage(settings.ts_coverage, allow_missing=allow_missing)
    for problem in problems:
        _emit(session, problem)
    if problems and not allow_missing:
        session.exitstatus = 1
        return None
    return present


def _typescript_entries_ok(session: pytest.Session, settings: GateSettings) -> bool:
    """`False` after failing the session for a `--riskratchet-ts-entry` that is not on disk.

    The CLI's rule at this door: a path named on the command line must exist. Only the
    flag is checked, not the resolved list, because a `[tool.riskratchet] ts_entry` key
    stays the engine's warning in the CLI too — failing a plugin session on a config
    default would turn a green gate red on a patch upgrade, and this release moves no
    verdicts.
    """
    from riskratchet.config import usable_ts_entries

    if not settings.typescript:
        return True
    rootdir = Path(str(session.config.rootpath))
    named = _resolved_all(rootdir, session.config.getoption("--riskratchet-ts-entry"))
    if not named:
        return True
    _, problems = usable_ts_entries(named)
    for problem in problems:
        _emit(session, problem)
    if problems:
        session.exitstatus = 1
        return False
    return True


def _report_or_fail(
    session: pytest.Session,
    settings: GateSettings,
    *,
    config_dir: Path,
    coverage_path: Path,
    ts_coverage: list[Path],
    redaction: RedactionConfig,
) -> RiskReport | None:
    """Build the report through the shared seam, or fail the session explaining why.

    `None` means "already reported, stop here". The exception set is the CLI boundary's
    (`cli._build_report_or_exit`): the absent `[typescript]` extra surfaces as an
    `ImportError` carrying the install hint, and an unreadable or malformed coverage
    report as `FileNotFoundError` / `ValueError`. Each is a setup problem, so it fails
    the session with the message rather than a traceback.
    """
    from riskratchet.pipeline import build_report

    try:
        return build_report(
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
            typescript=settings.typescript,
            ts_coverage_paths=ts_coverage,
            ts_entries=settings.ts_entry,
            on_churn_error=lambda message: _emit(session, f"riskratchet: {message}"),
            on_ts_warning=lambda message: _emit(session, f"typescript: {message}"),
            on_ts_error=lambda path, message: _emit(
                session, f"typescript: skipping {_disclosed(path, config_dir, redaction)}: {message}"
            ),
            on_warning=lambda path, message: _emit(
                session, f"warning: {_disclosed(path, config_dir, redaction)} {message}"
            ),
            on_error=lambda path, message: _emit(
                session, f"warning: skipping {_disclosed(path, config_dir, redaction)}: {message}"
            ),
        )
    except (ImportError, FileNotFoundError, ValueError) as exc:
        _emit(session, f"riskratchet: {exc}")
        session.exitstatus = 1
        return None


def _guarded_typescript_identity(
    session: pytest.Session,
    baseline: Baseline,
    report: RiskReport,
    *,
    settings: GateSettings,
    baseline_path: Path,
    ts_coverage: list[Path],
) -> tuple[Baseline, RiskReport]:
    """The plugin's copy of `cli._apply_baseline_guards`.

    Says when the baseline holds a language this run did not analyze — those entries
    simply vanish from the comparison, so a Python-only session over a mixed baseline
    would otherwise gate half the project and report a clean run. Says when the baseline's
    numbers were produced by a different scoring model, weight vector, churn window, churn
    availability or coverage presence, so a gate never compares two scores that were never
    measurements of the same thing. And when the baseline's TypeScript grammar differs from
    the runtime's, every persisted fingerprint is stale, so TypeScript is matched by id only
    and the exact re-baseline command is printed.

    **Per-door convention.** The plugin has no exit 2: a session failure is
    `session.exitstatus = 1`, which means "a gate tripped". So every disclosure here is a
    message and nothing more — the CLI's setup-error exit has no equivalent to route to, and
    borrowing exit 1 would report a failure the code did not cause. These are warnings at all
    three doors anyway, which is what makes the convention safe to state this plainly.
    """
    from riskratchet.baseline import (
        baseline_scored_without_churn,
        languages_not_scanned,
        scoring_model_stale,
        suppress_stale_typescript_renames,
        typescript_identity_stale,
    )

    for language, lost in languages_not_scanned(baseline, report).items():
        hint = (
            " (pass --riskratchet-typescript, or set typescript = true in [tool.riskratchet])"
            if language == "typescript"
            else ""
        )
        _emit(
            session,
            f"riskratchet: the baseline holds {lost} {language} entr{'y' if lost == 1 else 'ies'} "
            f"but this run analyzed no {language} — those functions are not being gated{hint}.",
        )
    reasons = scoring_model_stale(baseline, report)
    if reasons:
        _emit(session, "riskratchet: the baseline was not scored the way this run scores:")
        for reason in reasons:
            _emit(session, f"  - {reason}")
        _emit(session, "  comparing them anyway; re-baseline once the difference is the one you intended.")
        _emit(session, f"  re-baseline: {_rebaseline_command(settings, baseline_path, ts_coverage)}")
    elif baseline_scored_without_churn(baseline, report):
        _emit(
            session,
            "riskratchet: every function in the baseline has zero churn but this run measured some, "
            "so the baseline was probably written where git history was unavailable — churn will "
            "look like a regression everywhere.",
        )
        _emit(session, f"  re-baseline: {_rebaseline_command(settings, baseline_path, ts_coverage)}")
    if not settings.typescript or not typescript_identity_stale(baseline):
        return baseline, report
    _emit(
        session,
        "typescript: baseline TypeScript grammar/scheme differs from the runtime; matching TypeScript "
        "functions by id only (a grammar bump changes every fingerprint) — re-baseline recommended",
    )
    _emit(session, f"  re-baseline: {_rebaseline_command(settings, baseline_path, ts_coverage)}")
    return suppress_stale_typescript_renames(baseline, report)


def _rebaseline_command(settings: GateSettings, baseline_path: Path, ts_coverage: list[Path]) -> str:
    """The plugin's copy of `cli._rebaseline_command`: the exact command that rewrites this baseline.

    `--typescript` only when this session analyzed it — on a Python-only project the flag would
    hand back a command that exits 2 for want of the extra.
    """
    command = "riskratchet baseline " + " ".join(str(path) for path in settings.paths)
    if settings.typescript:
        command += " --typescript"
    for path in ts_coverage:
        command += f" --ts-coverage {path}"
    return f"{command} --output {baseline_path}"


def _gated(
    session: pytest.Session,
    report: RiskReport,
    baseline: Baseline,
    *,
    settings: GateSettings,
    config_dir: Path,
) -> list[Regression]:
    """`check`'s gate — `diff` + `regressions_from_diff`, one policy, one verdict — plus its
    disclosure: baseline entries under the scanned paths that this run did not reach
    (an `include` / `exclude` that hides a baselined file) are said out loud, counts only.
    The plugin used `compare` until 0.3.6; the diff is what knows what was *not* seen."""
    from riskratchet.baseline import (
        diff,
        left_the_gate_message,
        regressions_from_diff,
        unscanned_baseline_files,
        unscanned_files_message,
    )

    diff_report = diff(
        report,
        baseline,
        fail_regression_above=settings.fail_regression_above,
        fail_component_regression_above=settings.fail_component_regression_above,
        component_regression_gate=settings.component_regression_gate,
        groups=settings.groups,
    )
    entries, files = unscanned_baseline_files(
        diff_report, report=report, config_dir=config_dir, scan_roots=settings.paths
    )
    if entries:
        _emit(session, "riskratchet: " + unscanned_files_message(entries, files))
    left = left_the_gate_message(diff_report)
    if left is not None:
        _emit(session, "riskratchet: " + left)
    return regressions_from_diff(
        diff_report, fail_new_above=settings.fail_new_above, fail_existing_above=settings.fail_existing_above
    )


def _resolved_redaction(session: pytest.Session, cfg: dict[str, Any], config_dir: Path) -> RedactionConfig:
    """Resolve this run's redaction settings, once, as early as the config allows.

    Called from `pytest_sessionfinish` the moment `config_dir` is known, because two
    separate consumers need it: the per-file warning emitter (which runs *during*
    analysis) and the regressions table (which runs after). Resolving it at the second
    one, as this plugin did before 0.3.8, leaves the first unredacted.

    Messages emitted before this point cannot redact -- `resolve_redaction` reads the
    config file, so anything reporting a problem *with* that file necessarily precedes
    it. Those are setup errors addressed to whoever ran pytest, and they stay raw by
    the same rule the CLI applies.
    """
    from riskratchet.config import resolve_redaction

    return resolve_redaction(
        redact_paths=False,
        redact_qualnames=False,
        private_comment=False,
        redact_salt=None,
        cfg=cfg,
        config_dir=config_dir,
        warn=lambda message: _emit(session, message),
        no_redact_paths=bool(session.config.getoption("--riskratchet-no-redact-paths")),
        no_redact_qualnames=bool(session.config.getoption("--riskratchet-no-redact-qualnames")),
        no_private_comment=bool(session.config.getoption("--riskratchet-no-private-comment")),
    )


def _report_regressions(
    session: pytest.Session,
    regressions: list[Regression],
    *,
    redaction: RedactionConfig,
) -> None:
    """Render the regressions table, redacted if the project asked for that.

    Redaction is an output transform applied after the gate, so it can never change the
    verdict — but it does have to happen. `redact_regressions` exists for exactly this
    table, and the plugin used to print raw paths and qualnames straight into CI logs
    for repos running `redact_paths` / `private_comment`.
    """
    from riskratchet.redaction import redact_regressions
    from riskratchet.reporting import render_regressions_table

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


def _resolved_all(rootdir: Path, values: list[str] | None) -> list[Path] | None:
    """Anchor a repeatable option's values to the pytest rootdir; `None` when it was not passed."""
    if not values:
        return None
    return [_resolve(rootdir, value) for value in values]


def _resolved_one(rootdir: Path, value: object) -> Path | None:
    """Anchor a single option's value to the pytest rootdir; `None` when it was not passed.

    `None` is the whole point: it is what lets `[tool.riskratchet]` be consulted. An option
    that defaults to a real path hands this a value indistinguishable from one the user
    typed, and config loses every time (`config.resolve_gate_settings`).
    """
    return None if value is None else _resolve(rootdir, value)


def _rel_or_str(path: object, root: Path) -> str:
    from riskratchet._paths import relative_posix

    try:
        return relative_posix(Path(str(path)), root)
    except (ValueError, OSError):
        return str(path)


def _disclosed(path: object, config_dir: Path, redaction: RedactionConfig) -> str:
    """Name a file under analysis on the session's message stream.

    The plugin's half of `cli._Disclosures`, and it applies the same two rules: hash
    `relative_posix(path, config_dir)`, because that is the spelling `FunctionId.path`
    carries and `redact_function_id` hashes, so a warning's digest is the one its row
    in the regressions table shows; and use it only for disclosures *about the scanned
    code*, never for setup errors like a missing baseline, where a hashed filename
    would make the remediation unfollowable.
    """
    from riskratchet.redaction import redact_path_string

    return redact_path_string(_rel_or_str(path, config_dir), redaction)


def _emit(session: pytest.Session, message: str) -> None:
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(message)
    else:
        print(message)
