"""Typer entrypoint for the riskratchet CLI.

Each command is a thin shell: load config, call `analyze` (and friends), pick
a renderer, write to stdout or `--output`. Business logic lives in the other
modules; this file should stay easy to scan.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer

from riskratchet import __version__
from riskratchet.auto_coverage import DEFAULT_CACHE_PATH
from riskratchet.baseline import (
    BaselineVersionError,
    baseline_from_report,
    languages_not_scanned,
    load_baseline,
    regressions_above_threshold,
    regressions_from_diff,
    save_baseline,
    suppress_stale_typescript_renames,
    typescript_identity_stale,
)
from riskratchet.baseline import (
    diff as diff_baseline,
)
from riskratchet.config import (
    CONFIG_SCHEMA_URL,
    _anchor_config_path,
    _discover_config,
    _ensure_coverage_map_exists,
    _ensure_ts_coverage_exists,
    _format_setup_error,
    _load_config_strict,
    _resolve_coverage,
    _resolved_bool,
    _resolved_churn_days,
    _resolved_config_payload,
    _resolved_coverage_map,
    _resolved_float,
    _resolved_groups,
    _resolved_missing_coverage,
    _resolved_optional_float,
    _resolved_paths,
    _resolved_weights,
    invalid_config_values,
    resolve_gate_settings,
    resolved_ts_paths,
    resolved_typescript,
    unknown_config_keys,
)
from riskratchet.config import (
    resolve_redaction as _resolve_redaction,
)
from riskratchet.diagnostics import Diagnostics, write_debug_json
from riskratchet.doctor import CheckStatus, DoctorCheck, diagnose, summarize
from riskratchet.git import is_shallow_repo
from riskratchet.init import (
    InitOutcome,
    RunnerKind,
    detect_test_runner,
    render_ci_snippet,
    write_starter_config,
)
from riskratchet.models import (
    Baseline,
    DiffReport,
    FunctionRisk,
    Regression,
    RegressionKind,
    RiskReport,
    Severity,
)
from riskratchet.pipeline import build_report
from riskratchet.redaction import (
    RedactionConfig,
    redact_diagnostics,
    redact_diff,
    redact_function,
    redact_path_string,
    redact_regressions,
    redact_report,
)
from riskratchet.reporting import (
    SourceLinks,
    render_diff_github,
    render_diff_json,
    render_diff_markdown,
    render_diff_pr_comment,
    render_diff_summary_json,
    render_diff_summary_text,
    render_diff_table,
    render_function_explanation,
    render_function_json,
    render_function_summary_json,
    render_regressions_github,
    render_regressions_json,
    render_regressions_markdown,
    render_regressions_pr_comment,
    render_regressions_sarif,
    render_regressions_summary_json,
    render_regressions_summary_text,
    render_regressions_table,
    render_report_github,
    render_report_json,
    render_report_markdown,
    render_report_pr_comment,
    render_report_sarif,
    render_report_summary_json,
    render_report_summary_text,
    render_report_table,
)
from riskratchet.scoring import severity

VALID_FORMATS = ("table", "json", "markdown", "sarif", "github", "pr-comment")
VALID_BASELINE_FORMATS = ("riskratchet",)
VALID_FAIL_SEVERITIES = ("low", "medium", "high", "critical")

# Typer renders help through Rich, which parses `[...]` as a style tag and drops it: the paths help
# used to render as "Falls back to  paths if omitted." and the TypeScript hint as the *wrong* command
# `pip install 'riskratchet'`. `\[` escapes the bracket. Interpolating these from the tuples above
# also keeps the documented choices from drifting when a format is added.
_CONFIG_TABLE = "\\[tool.riskratchet]"
_TYPESCRIPT_EXTRA = "riskratchet\\[typescript]"
_PATHS_HELP = "Files or directories to {action}. Falls back to " + _CONFIG_TABLE + " paths if omitted."
FORMAT_HELP = f"Output format. One of: {', '.join(VALID_FORMATS)}."
BASELINE_FORMAT_HELP = (
    f"Baseline input format. Currently only {' or '.join(VALID_BASELINE_FORMATS)!r} is supported."
)
FAIL_SEVERITY_HELP = (
    f"Fail when any function reaches this severity. One of: {', '.join(VALID_FAIL_SEVERITIES)}."
)

# Shared analysis options. `scan` documented these from the start while check/diff/baseline declared
# bare `typer.Option("--flag")` with no help at all, so ~12 flags per command rendered blank in
# `--help`. Declaring the wording once keeps the four commands from drifting apart again.
CoverageOption = Annotated[Path | None, typer.Option("--coverage", help="Path to coverage.json.")]
ConfigOption = Annotated[Path | None, typer.Option("--config", help="Path to pyproject.toml.")]
FormatOption = Annotated[str, typer.Option("--format", help=FORMAT_HELP)]
OutputOption = Annotated[Path | None, typer.Option("--output", help="Write output to file.")]
IncludeOption = Annotated[list[str] | None, typer.Option("--include", help="Glob include patterns.")]
ExcludeOption = Annotated[list[str] | None, typer.Option("--exclude", help="Glob exclude patterns.")]
AllowOption = Annotated[
    list[str] | None,
    typer.Option("--allow", help="Suppress matching functions or path globs from reporting/gating."),
]
NoGitOption = Annotated[
    bool, typer.Option("--no-git", help="Skip git history; churn scores as zero for every function.")
]
FailNewAboveOption = Annotated[
    float | None,
    typer.Option("--fail-new-above", help="Fail when a function absent from the baseline scores above N."),
]
FailRegressionAboveOption = Annotated[
    float | None,
    typer.Option("--fail-regression-above", help="Fail when a function's score grows by more than N."),
]
FailExistingAboveOption = Annotated[
    float | None,
    typer.Option(
        "--fail-existing-above",
        help="Fail when a function already in the baseline scores above N.",
    ),
]
FailComponentRegressionAboveOption = Annotated[
    float | None,
    typer.Option(
        "--fail-component-regression-above",
        help="Fail when any single risk component grows by more than N. Default 15.",
    ),
]
NoComponentRegressionGateOption = Annotated[
    bool,
    typer.Option(
        "--no-component-regression-gate",
        help="Disable per-component regression checks.",
    ),
]

# Shared TypeScript-backend options (since 0.3.0), reused by check/diff (scan declares its own so it
# can also carry the deprecated --experimental-typescript alias).
TypescriptOption = Annotated[
    bool,
    typer.Option(
        "--typescript",
        help="Also analyze and score TypeScript functions (since 0.3.0), mixed into the scored "
        'functions with `language: "typescript"`. Same as `typescript = true` in '
        "\\[tool.riskratchet] (since 0.3.6). Needs "
        f"`pip install '{_TYPESCRIPT_EXTRA}'`.",
    ),
]
NoTypescriptOption = Annotated[
    bool,
    typer.Option(
        "--no-typescript",
        help="Skip TypeScript even when \\[tool.riskratchet] sets typescript = true (since 0.3.6).",
    ),
]
TsCoverageOption = Annotated[
    list[Path] | None,
    typer.Option(
        "--ts-coverage",
        help="Istanbul/nyc/LCOV coverage report(s) to give TypeScript functions line/branch coverage "
        "(format auto-detected). Repeatable. Only used with --typescript.",
    ),
]
TsEntryOption = Annotated[
    list[Path] | None,
    typer.Option(
        "--ts-entry",
        help="TypeScript package entry file(s) (e.g. src/index.ts) to narrow public surface to what "
        "is reachable through barrel re-exports. Repeatable. Only used with --typescript.",
    ),
]

app = typer.Typer(
    help="A maintainability ratchet for AI-assisted Python and TypeScript.",
    no_args_is_help=True,
    add_completion=False,
)
config_app = typer.Typer(help="Inspect and validate riskratchet configuration.", no_args_is_help=True)
app.add_typer(config_app, name="config")


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: Annotated[bool, typer.Option("--version", help="Show version and exit.")] = False,
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


@config_app.command("validate")
def config_validate(
    config: Annotated[Path, typer.Option("--config", help="Path to pyproject.toml.")] = Path(
        "pyproject.toml"
    ),
) -> None:
    """Validate `\\[tool.riskratchet]` configuration."""
    try:
        _load_config_strict(config)
    except ValueError as exc:
        typer.secho(f"config error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"valid riskratchet config: {config}")


@config_app.command("show")
def config_show(
    config: Annotated[Path, typer.Option("--config", help="Path to pyproject.toml.")] = Path(
        "pyproject.toml"
    ),
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON.")] = False,
) -> None:
    """Show resolved riskratchet configuration."""
    try:
        cfg = _load_config_strict(config)
    except ValueError as exc:
        typer.secho(f"config error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    if not json_output:
        typer.secho("config show currently supports --json only.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    typer.echo(
        json.dumps(
            {
                "$schema": CONFIG_SCHEMA_URL,
                "version": __version__,
                "config_path": str(config),
                "config": _resolved_config_payload(cfg, config.resolve().parent),
            },
            indent=2,
        )
    )


@app.command()
def scan(
    paths: Annotated[
        list[Path] | None,
        typer.Argument(help=_PATHS_HELP.format(action="scan")),
    ] = None,
    coverage: CoverageOption = None,
    coverage_map: Annotated[
        list[str] | None,
        typer.Option(
            "--coverage-map",
            help="Per-prefix coverage path, repeatable: --coverage-map packages/a=cov-a.json.",
        ),
    ] = None,
    config: ConfigOption = None,
    format: FormatOption = "table",
    json_output: Annotated[
        bool, typer.Option("--json", help="Shortcut for --format json. Overrides --format.")
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option(
            "--quiet",
            "-q",
            help="Suppress the trailing summary line on table output (pipe-friendly).",
        ),
    ] = False,
    output: OutputOption = None,
    summary: Annotated[bool, typer.Option("--summary", help="Emit aggregate summary only.")] = False,
    include: IncludeOption = None,
    exclude: ExcludeOption = None,
    allow: Annotated[
        list[str] | None,
        typer.Option("--allow", help="Suppress matching functions or path globs from reporting/gating."),
    ] = None,
    no_git: NoGitOption = False,
    churn_days: Annotated[
        int | None,
        typer.Option("--churn-days", help="Churn window in days. Default 90."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="Max table rows; 0 for all.")] = 20,
    top: Annotated[
        int | None,
        typer.Option("--top", help="Max rows/functions to emit; alias for --limit."),
    ] = None,
    min_score: Annotated[
        float | None,
        typer.Option("--min-score", help="Hide functions below this score."),
    ] = None,
    fail_above: Annotated[
        float | None,
        typer.Option("--fail-above", help="Exit 1 if any emitted function score is greater than this value."),
    ] = None,
    fail_severity: Annotated[
        str | None,
        typer.Option("--fail-severity", help=FAIL_SEVERITY_HELP),
    ] = None,
    missing_coverage: Annotated[
        str | None,
        typer.Option("--missing-coverage", help="How to handle missing file coverage."),
    ] = None,
    no_auto_cov: Annotated[
        bool,
        typer.Option(
            "--no-auto-cov",
            help="Skip auto-generating coverage by running the test command.",
        ),
    ] = False,
    repo_url: Annotated[
        str | None,
        typer.Option("--repo-url", help="Repository URL for markdown links."),
    ] = None,
    commit_ref: Annotated[
        str | None,
        typer.Option("--commit-ref", help="Commit ref for markdown links."),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Emit structured run diagnostics to stderr (since 0.2.9 P11)."),
    ] = False,
    debug_json: Annotated[
        bool,
        typer.Option("--debug-json", help="Emit diagnostics as a JSON envelope to stderr (since 0.2.9 P11)."),
    ] = False,
    debug_json_file: Annotated[
        Path | None,
        typer.Option("--debug-json-file", help="Write the --debug-json envelope to this file instead."),
    ] = None,
    redact_paths: Annotated[
        bool,
        typer.Option("--redact-paths", help="Hash source paths in output (since 0.2.9 P12)."),
    ] = False,
    redact_qualnames: Annotated[
        bool,
        typer.Option("--redact-qualnames", help="Hash function qualnames in output (since 0.2.9 P12)."),
    ] = False,
    private_comment: Annotated[
        bool,
        typer.Option(
            "--private-comment",
            help="Preset: redact paths + qualnames and suppress source links (since 0.2.9 P12).",
        ),
    ] = False,
    redact_salt: Annotated[
        str | None,
        typer.Option("--redact-salt", help="Salt for redaction hashes (or RISKRATCHET_REDACT_SALT)."),
    ] = None,
    typescript: TypescriptOption = False,
    no_typescript: NoTypescriptOption = False,
    experimental_typescript: Annotated[
        bool,
        typer.Option(
            "--experimental-typescript",
            help="Deprecated alias for --typescript (TypeScript is scored since 0.3.0).",
            hidden=True,
        ),
    ] = False,
    ts_coverage: Annotated[
        list[Path] | None,
        typer.Option(
            "--ts-coverage",
            help="Istanbul/nyc coverage-final.json or LCOV lcov.info to give TypeScript functions "
            "line/branch coverage (format auto-detected per file). Repeatable (one per package in a "
            "monorepo; formats may be mixed). Only used with --typescript; separate from --coverage "
            "(which is Python).",
        ),
    ] = None,
    ts_entry: Annotated[
        list[Path] | None,
        typer.Option(
            "--ts-entry",
            help="Package entry file(s) (e.g. src/index.ts) used to narrow TypeScript public surface "
            "to what is reachable through barrel re-exports. Repeatable. Only used with --typescript; "
            "falls back to package.json / index.ts, and to file-level export flags when no entry is "
            "found.",
        ),
    ] = None,
) -> None:
    """Scan files and report risk; never fails."""
    cfg, config_dir = _discover_config(config)
    _enforce_config_or_exit(cfg)
    effective_format = _effective_format(format, json_output)
    redaction = _resolve_redaction(
        redact_paths=redact_paths,
        redact_qualnames=redact_qualnames,
        private_comment=private_comment,
        redact_salt=redact_salt,
        cfg=cfg,
        config_dir=config_dir,
    )
    diag = Diagnostics(command="scan")
    resolved_paths = _resolved_paths(paths, cfg, config_dir)
    _check_paths_exist(resolved_paths, paths_arg=paths, configured=cfg.get("paths"))
    resolved_include = include or cfg.get("include", [])
    resolved_exclude = exclude or cfg.get("exclude", [])
    resolved_allow = allow or cfg.get("allow", [])
    resolved_churn_days = _resolved_churn_days(churn_days, cfg)
    ts = _resolve_ts_settings(
        typescript,
        no_typescript,
        experimental_typescript,
        ts_coverage=ts_coverage,
        ts_entry=ts_entry,
        cfg=cfg,
        config_dir=config_dir,
        allow_missing=False,
        required=False,
    )
    coverage_path, resolved_coverage_map = _resolve_coverage_inputs(
        coverage,
        coverage_map,
        cfg=cfg,
        config_dir=config_dir,
        sources=resolved_paths,
        no_auto_cov=no_auto_cov,
        required=False,
        # Not `allow_missing=True`: that is redundant with `required=False` and would
        # also excuse a *typo'd* --coverage path, which is a usage error here as much
        # as a nonexistent scan path is.
        allow_missing=False,
        map_allow_missing=True,
        diag=diag,
        ts=ts,
        include=resolved_include,
        exclude=resolved_exclude,
    )
    _emit_diagnostics_banner(
        command="scan",
        scan_roots=resolved_paths,
        coverage_path=coverage_path,
        config_dir=config_dir,
        coverage_map=resolved_coverage_map,
        redaction=redaction,
    )
    report = _build_report_or_exit(
        resolved_paths,
        config_dir=config_dir,
        coverage_path=coverage_path,
        coverage_map=resolved_coverage_map or None,
        include=resolved_include,
        exclude=resolved_exclude,
        allow=resolved_allow,
        use_git=not no_git,
        churn_days=resolved_churn_days,
        cfg=cfg,
        missing_coverage=missing_coverage,
        ts_enabled=ts.enabled,
        ts_coverage=ts.coverage,
        ts_entry=ts.entry,
    )
    filtered = _filtered_report(report, min_score=min_score, top=top or (None if limit == 0 else limit))
    _populate_run_diagnostics(
        diag,
        report=report,
        reported_functions=len(filtered.functions),
        include=resolved_include,
        exclude=resolved_exclude,
        allow=resolved_allow,
        use_git=not no_git,
        churn_days=resolved_churn_days,
        root=config_dir,
    )
    _warn_empty_scan(filtered, command="scan")
    links = _links_for(repo_url, commit_ref, redaction)
    filtered = redact_report(filtered, redaction)
    _emit_report(
        filtered,
        format=effective_format,
        output=output,
        limit=0,
        quiet=quiet,
        min_score=min_score,
        links=links,
        summary=summary,
    )
    if effective_format == "table" and not quiet and not summary and output is None:
        baseline_file = _anchor_config_path(Path(cfg.get("baseline", ".riskratchet.json")), config_dir)
        _emit_scan_next_step_footer(filtered, baseline_file=baseline_file, config_present=bool(cfg))
    _emit_diagnostics(
        diag,
        verbose=verbose,
        debug_json=debug_json,
        debug_json_file=debug_json_file,
        redaction=redaction,
    )
    _exit_for_scan_gate(filtered, fail_above=fail_above, fail_severity=fail_severity)


@app.command()
def baseline(
    paths: Annotated[
        list[Path] | None,
        typer.Argument(help=_PATHS_HELP.format(action="baseline")),
    ] = None,
    coverage: CoverageOption = None,
    coverage_map: Annotated[
        list[str] | None,
        typer.Option("--coverage-map", help="Per-prefix coverage path, repeatable."),
    ] = None,
    config: ConfigOption = None,
    output: Annotated[Path | None, typer.Option("--output", help="Where to write the baseline JSON.")] = None,
    include: IncludeOption = None,
    exclude: ExcludeOption = None,
    allow: AllowOption = None,
    no_git: NoGitOption = False,
    churn_days: Annotated[
        int | None,
        typer.Option("--churn-days", help="Churn window in days. Default 90."),
    ] = None,
    missing_coverage: Annotated[
        str | None,
        typer.Option("--missing-coverage", help="How to handle missing file coverage."),
    ] = None,
    allow_missing_coverage: Annotated[
        bool,
        typer.Option(
            "--allow-missing-coverage",
            help="Allow baselining without configured coverage data.",
        ),
    ] = False,
    no_auto_cov: Annotated[
        bool,
        typer.Option(
            "--no-auto-cov",
            help="Skip auto-generating coverage by running the test command.",
        ),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Emit structured run diagnostics to stderr (since 0.2.9 P11)."),
    ] = False,
    debug_json: Annotated[
        bool,
        typer.Option("--debug-json", help="Emit diagnostics as a JSON envelope to stderr (since 0.2.9 P11)."),
    ] = False,
    debug_json_file: Annotated[
        Path | None,
        typer.Option("--debug-json-file", help="Write the --debug-json envelope to this file instead."),
    ] = None,
    typescript: TypescriptOption = False,
    no_typescript: NoTypescriptOption = False,
    ts_coverage: TsCoverageOption = None,
    ts_entry: TsEntryOption = None,
) -> None:
    """Compute current risk and save it as the new baseline.

    Redaction flags are intentionally not accepted here: the baseline file is
    the source of truth for future rename matching and must never be hashed.
    """
    cfg, config_dir = _discover_config(config)
    _enforce_config_or_exit(cfg)
    diag = Diagnostics(command="baseline")
    resolved_paths = _resolved_paths(paths, cfg, config_dir)
    _check_paths_exist(resolved_paths, paths_arg=paths, configured=cfg.get("paths"))
    resolved_include = include or cfg.get("include", [])
    resolved_exclude = exclude or cfg.get("exclude", [])
    resolved_allow = allow or cfg.get("allow", [])
    resolved_churn_days = _resolved_churn_days(churn_days, cfg)
    allow_missing = _resolved_bool(allow_missing_coverage, cfg.get("allow_missing_coverage"))
    ts = _resolve_ts_settings(
        typescript,
        no_typescript,
        False,
        ts_coverage=ts_coverage,
        ts_entry=ts_entry,
        cfg=cfg,
        config_dir=config_dir,
        allow_missing=allow_missing,
        required=True,
    )
    coverage_path, resolved_coverage_map = _resolve_coverage_inputs(
        coverage,
        coverage_map,
        cfg=cfg,
        config_dir=config_dir,
        sources=resolved_paths,
        no_auto_cov=no_auto_cov,
        required=True,
        allow_missing=allow_missing,
        map_allow_missing=allow_missing,
        diag=diag,
        ts=ts,
        include=resolved_include,
        exclude=resolved_exclude,
    )
    _emit_diagnostics_banner(
        command="baseline",
        scan_roots=resolved_paths,
        coverage_path=coverage_path,
        config_dir=config_dir,
        coverage_map=resolved_coverage_map,
        redaction=RedactionConfig(),
    )
    report = _build_report_or_exit(
        resolved_paths,
        config_dir=config_dir,
        coverage_path=coverage_path,
        coverage_map=resolved_coverage_map or None,
        include=resolved_include,
        exclude=resolved_exclude,
        allow=resolved_allow,
        use_git=not no_git,
        churn_days=resolved_churn_days,
        cfg=cfg,
        missing_coverage=missing_coverage,
        ts_enabled=ts.enabled,
        ts_coverage=ts.coverage,
        ts_entry=ts.entry,
    )
    _populate_run_diagnostics(
        diag,
        report=report,
        reported_functions=len(report.functions),
        include=resolved_include,
        exclude=resolved_exclude,
        allow=resolved_allow,
        use_git=not no_git,
        churn_days=resolved_churn_days,
        root=config_dir,
    )
    target = output or _anchor_config_path(Path(cfg.get("baseline", ".riskratchet.json")), config_dir)
    _refuse_to_erase_baseline(report, target)
    _save_baseline_or_exit(baseline_from_report(report), target)
    _emit_diagnostics(
        diag,
        verbose=verbose,
        debug_json=debug_json,
        debug_json_file=debug_json_file,
        redaction=RedactionConfig(),
    )
    typer.echo(f"wrote baseline with {len(report.functions)} functions to {target}")


@app.command()
def check(
    paths: Annotated[
        list[Path] | None,
        typer.Argument(help=_PATHS_HELP.format(action="check")),
    ] = None,
    coverage: CoverageOption = None,
    coverage_map: Annotated[
        list[str] | None,
        typer.Option("--coverage-map", help="Per-prefix coverage path, repeatable."),
    ] = None,
    baseline_path: Annotated[Path | None, typer.Option("--baseline", help="Path to baseline JSON.")] = None,
    config: ConfigOption = None,
    format: FormatOption = "table",
    json_output: Annotated[
        bool, typer.Option("--json", help="Shortcut for --format json. Overrides --format.")
    ] = False,
    baseline_format: Annotated[
        str,
        typer.Option(
            "--baseline-format",
            help=BASELINE_FORMAT_HELP,
        ),
    ] = "riskratchet",
    output: OutputOption = None,
    summary: Annotated[bool, typer.Option("--summary", help="Emit aggregate summary only.")] = False,
    fail_above: Annotated[
        float | None,
        typer.Option(
            "--fail-above",
            help=(
                "Fail when any function's current score exceeds N. Makes --baseline "
                "optional; ignored when a baseline is resolved."
            ),
        ),
    ] = None,
    fail_new_above: FailNewAboveOption = None,
    fail_regression_above: FailRegressionAboveOption = None,
    fail_existing_above: FailExistingAboveOption = None,
    fail_component_regression_above: FailComponentRegressionAboveOption = None,
    no_component_regression_gate: NoComponentRegressionGateOption = False,
    include: IncludeOption = None,
    exclude: ExcludeOption = None,
    allow: AllowOption = None,
    no_git: NoGitOption = False,
    churn_days: Annotated[
        int | None,
        typer.Option("--churn-days", help="Churn window in days. Default 90."),
    ] = None,
    missing_coverage: Annotated[
        str | None,
        typer.Option("--missing-coverage", help="How to handle missing file coverage."),
    ] = None,
    allow_missing_coverage: Annotated[
        bool,
        typer.Option(
            "--allow-missing-coverage",
            help="Allow checking without configured coverage data.",
        ),
    ] = False,
    no_auto_cov: Annotated[
        bool,
        typer.Option(
            "--no-auto-cov",
            help="Skip auto-generating coverage by running the test command.",
        ),
    ] = False,
    repo_url: Annotated[
        str | None,
        typer.Option("--repo-url", help="Repository URL for markdown links."),
    ] = None,
    commit_ref: Annotated[
        str | None,
        typer.Option("--commit-ref", help="Commit ref for markdown links."),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Emit structured run diagnostics to stderr (since 0.2.9 P11)."),
    ] = False,
    debug_json: Annotated[
        bool,
        typer.Option("--debug-json", help="Emit diagnostics as a JSON envelope to stderr (since 0.2.9 P11)."),
    ] = False,
    debug_json_file: Annotated[
        Path | None,
        typer.Option("--debug-json-file", help="Write the --debug-json envelope to this file instead."),
    ] = None,
    redact_paths: Annotated[
        bool,
        typer.Option("--redact-paths", help="Hash source paths in output (since 0.2.9 P12)."),
    ] = False,
    redact_qualnames: Annotated[
        bool,
        typer.Option("--redact-qualnames", help="Hash function qualnames in output (since 0.2.9 P12)."),
    ] = False,
    private_comment: Annotated[
        bool,
        typer.Option(
            "--private-comment",
            help="Preset: redact paths + qualnames and suppress source links (since 0.2.9 P12).",
        ),
    ] = False,
    redact_salt: Annotated[
        str | None,
        typer.Option("--redact-salt", help="Salt for redaction hashes (or RISKRATCHET_REDACT_SALT)."),
    ] = None,
    typescript: TypescriptOption = False,
    no_typescript: NoTypescriptOption = False,
    ts_coverage: TsCoverageOption = None,
    ts_entry: TsEntryOption = None,
) -> None:
    """Fail (exit 1) when risk regresses past tolerance."""
    cfg, config_dir = _discover_config(config)
    _enforce_config_or_exit(cfg)
    effective_format = _effective_format(format, json_output)
    redaction = _resolve_redaction(
        redact_paths=redact_paths,
        redact_qualnames=redact_qualnames,
        private_comment=private_comment,
        redact_salt=redact_salt,
        cfg=cfg,
        config_dir=config_dir,
    )
    diag = Diagnostics(command="check")
    _validate_baseline_format(baseline_format)
    fail_above_resolved = _resolved_optional_float(fail_above, cfg.get("fail_above"))
    if fail_above_resolved is not None and not (0 < fail_above_resolved <= 100):
        typer.secho(
            "--fail-above must be a number in (0, 100].",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    baseline_file = baseline_path or _anchor_config_path(
        Path(cfg.get("baseline", ".riskratchet.json")), config_dir
    )
    baseline_present = baseline_file.exists()
    if not baseline_present and fail_above_resolved is None:
        typer.secho(
            _format_setup_error(
                f"riskratchet: baseline file not found: {baseline_file}",
                [
                    ("Create a baseline of current risk:", "riskratchet baseline"),
                    (
                        "Gate on an absolute threshold (no baseline required):",
                        "riskratchet check --fail-above 60",
                    ),
                ],
            ),
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    if baseline_present and fail_above_resolved is not None:
        typer.secho(
            f"warning: --fail-above ignored when a baseline is present ({baseline_file}); "
            f"baseline gate is authoritative. Use --fail-existing-above for a "
            f"baseline-aware absolute threshold.",
            fg=typer.colors.YELLOW,
            err=True,
        )
    old = _load_baseline_or_exit(baseline_file) if baseline_present else None
    diag.set_baseline(
        path=str(baseline_file),
        present=baseline_present,
        entry_count=len(old.entries) if old is not None else None,
    )
    resolved_paths = _resolved_paths(paths, cfg, config_dir)
    _check_paths_exist(resolved_paths, paths_arg=paths, configured=cfg.get("paths"))
    resolved_include = include or cfg.get("include", [])
    resolved_exclude = exclude or cfg.get("exclude", [])
    resolved_allow = allow or cfg.get("allow", [])
    resolved_churn_days = _resolved_churn_days(churn_days, cfg)
    allow_missing = _resolved_bool(allow_missing_coverage, cfg.get("allow_missing_coverage"))
    ts = _resolve_ts_settings(
        typescript,
        no_typescript,
        False,
        ts_coverage=ts_coverage,
        ts_entry=ts_entry,
        cfg=cfg,
        config_dir=config_dir,
        allow_missing=allow_missing,
        required=True,
    )
    coverage_path, resolved_coverage_map = _resolve_coverage_inputs(
        coverage,
        coverage_map,
        cfg=cfg,
        config_dir=config_dir,
        sources=resolved_paths,
        no_auto_cov=no_auto_cov,
        required=True,
        allow_missing=allow_missing,
        map_allow_missing=allow_missing,
        diag=diag,
        ts=ts,
        include=resolved_include,
        exclude=resolved_exclude,
    )
    _emit_diagnostics_banner(
        command="check",
        scan_roots=resolved_paths,
        coverage_path=coverage_path,
        config_dir=config_dir,
        coverage_map=resolved_coverage_map,
        redaction=redaction,
    )
    report = _build_report_or_exit(
        resolved_paths,
        config_dir=config_dir,
        coverage_path=coverage_path,
        coverage_map=resolved_coverage_map or None,
        include=resolved_include,
        exclude=resolved_exclude,
        allow=resolved_allow,
        use_git=not no_git,
        churn_days=resolved_churn_days,
        cfg=cfg,
        missing_coverage=missing_coverage,
        ts_enabled=ts.enabled,
        ts_coverage=ts.coverage,
        ts_entry=ts.entry,
    )
    if old is not None:
        old, report = _apply_ts_identity_guard(
            old,
            report,
            ts_enabled=ts.enabled,
            paths=resolved_paths,
            baseline_file=baseline_file,
            ts_coverage=ts.coverage,
        )
    _populate_run_diagnostics(
        diag,
        report=report,
        reported_functions=len(report.functions),
        include=resolved_include,
        exclude=resolved_exclude,
        allow=resolved_allow,
        use_git=not no_git,
        churn_days=resolved_churn_days,
        root=config_dir,
    )
    _require_gateable_functions(
        report, command="check", baseline_entries=len(old.entries) if old is not None else 0
    )
    fail_new_above_val = _resolved_float(fail_new_above, cfg.get("fail_new_above"), default=50.0)
    fail_existing_above_val = _resolved_optional_float(fail_existing_above, cfg.get("fail_existing_above"))
    diff_report: DiffReport | None
    if old is not None:
        diff_report = diff_baseline(
            report,
            old,
            fail_regression_above=_resolved_float(
                fail_regression_above, cfg.get("fail_regression_above"), default=5.0
            ),
            fail_component_regression_above=_resolved_float(
                fail_component_regression_above,
                cfg.get("fail_component_regression_above"),
                default=15.0,
            ),
            component_regression_gate=(
                not no_component_regression_gate
                and _resolved_bool(True, cfg.get("component_regression_gate"), default=True)
            ),
            groups=_resolved_groups(cfg),
        )
        regressions = regressions_from_diff(
            diff_report,
            fail_new_above=fail_new_above_val,
            fail_existing_above=fail_existing_above_val,
        )
    else:
        assert fail_above_resolved is not None
        diff_report = None
        regressions = regressions_above_threshold(report, threshold=fail_above_resolved)
    if redaction.active:
        # Redact the diff first (it carries the structured previous ids the
        # reason strings embed), then re-derive regressions so they inherit the
        # scrubbed reasons. The no-baseline path's reasons carry no foreign
        # targets, so redacting the regressions directly is sufficient.
        if diff_report is not None:
            diff_report = redact_diff(diff_report, redaction)
            regressions = regressions_from_diff(
                diff_report,
                fail_new_above=fail_new_above_val,
                fail_existing_above=fail_existing_above_val,
            )
        else:
            regressions = redact_regressions(regressions, redaction)
    links = _links_for(repo_url, commit_ref, redaction)
    if summary:
        rendered = (
            render_regressions_summary_json(regressions, diff_report=diff_report)
            if effective_format == "json"
            else render_regressions_summary_text(regressions, diff_report=diff_report)
        )
    elif effective_format == "pr-comment":
        # P8 (since 0.2.8): no-baseline mode renders the regressions-only
        # PR comment instead of bailing out, so the format works in both
        # baseline and `--fail-above` modes. Both modes render the same thing —
        # the set the gate acted on — because a comment posted beside the exit
        # code must not contradict it; the diff rides along as context.
        rendered = render_regressions_pr_comment(regressions, links=links, diff_report=diff_report)
    else:
        rendered = _render_regressions(regressions, format=effective_format, links=links)
    _write(rendered, output)
    _emit_diagnostics(
        diag,
        verbose=verbose,
        debug_json=debug_json,
        debug_json_file=debug_json_file,
        redaction=redaction,
    )
    if regressions:
        if old is not None:
            _emit_regression_hint(regressions, baseline_file=baseline_file)
        else:
            assert fail_above_resolved is not None
            _emit_above_threshold_hint(regressions, threshold=fail_above_resolved)
        raise typer.Exit(code=1)


@app.command()
def explain(
    target: Annotated[str, typer.Argument(help="Function target as `path/to/file.py::qualname`.")],
    coverage: CoverageOption = None,
    config: ConfigOption = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Emit JSON envelope (since 0.2.8 P9). Pairs with --summary."),
    ] = False,
    summary: Annotated[
        bool,
        typer.Option(
            "--summary",
            help="Emit aggregate summary only (since 0.2.8 P9). Pairs with --json.",
        ),
    ] = False,
    no_git: NoGitOption = False,
    churn_days: Annotated[
        int | None,
        typer.Option("--churn-days", help="Churn window in days. Default 90."),
    ] = None,
    no_auto_cov: Annotated[
        bool,
        typer.Option(
            "--no-auto-cov",
            help="Skip auto-generating coverage by running the test command.",
        ),
    ] = False,
    repo_url: Annotated[
        str | None,
        typer.Option("--repo-url", help="Repository URL for source links (since 0.2.8 P10)."),
    ] = None,
    commit_ref: Annotated[
        str | None,
        typer.Option("--commit-ref", help="Commit ref for source links (since 0.2.8 P10)."),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Emit structured run diagnostics to stderr (since 0.2.9 P11)."),
    ] = False,
    debug_json: Annotated[
        bool,
        typer.Option("--debug-json", help="Emit diagnostics as a JSON envelope to stderr (since 0.2.9 P11)."),
    ] = False,
    debug_json_file: Annotated[
        Path | None,
        typer.Option("--debug-json-file", help="Write the --debug-json envelope to this file instead."),
    ] = None,
    redact_paths: Annotated[
        bool,
        typer.Option("--redact-paths", help="Hash source paths in output (since 0.2.9 P12)."),
    ] = False,
    redact_qualnames: Annotated[
        bool,
        typer.Option("--redact-qualnames", help="Hash function qualnames in output (since 0.2.9 P12)."),
    ] = False,
    private_comment: Annotated[
        bool,
        typer.Option(
            "--private-comment",
            help="Preset: redact paths + qualnames and suppress source links (since 0.2.9 P12).",
        ),
    ] = False,
    redact_salt: Annotated[
        str | None,
        typer.Option("--redact-salt", help="Salt for redaction hashes (or RISKRATCHET_REDACT_SALT)."),
    ] = None,
    coverage_map: Annotated[
        list[str] | None,
        typer.Option(
            "--coverage-map",
            help="Per-prefix coverage path, repeatable: --coverage-map packages/a=cov-a.json.",
        ),
    ] = None,
    missing_coverage: Annotated[
        str | None,
        typer.Option("--missing-coverage", help="How to handle missing file coverage."),
    ] = None,
    typescript: TypescriptOption = False,
    no_typescript: NoTypescriptOption = False,
    ts_coverage: TsCoverageOption = None,
    ts_entry: TsEntryOption = None,
) -> None:
    """Print full risk breakdown for one function."""
    if "::" not in target:
        raise typer.BadParameter("target must be `path::qualname` (e.g. src/foo.py::Bar.baz)")
    cfg, config_dir = _discover_config(config)
    _enforce_config_or_exit(cfg)
    diag = Diagnostics(command="explain")
    file_part, _ = target.split("::", 1)
    # The target is a repo-relative *identity* — `path::qualname` is exactly what
    # `check` and `diff` print — so it anchors to the config directory like any other
    # config-sourced path. Run from a nested package, the cwd-relative reading made
    # `explain` reject the very target the other commands had just emitted. A
    # cwd-relative spelling still resolves, so existing invocations keep working.
    file_path = _anchor_config_path(Path(file_part), config_dir)
    if not file_path.exists():
        file_path = Path(file_part)
    resolved_coverage_map = _resolved_coverage_map(coverage_map, cfg, config_dir)
    ts = _resolve_ts_settings(
        typescript,
        no_typescript,
        False,
        ts_coverage=ts_coverage,
        ts_entry=ts_entry,
        cfg=cfg,
        config_dir=config_dir,
        allow_missing=False,
        required=False,
    )
    coverage_path = _resolve_coverage(
        coverage,
        cfg,
        sources=[file_path],
        no_auto_cov=no_auto_cov,
        required=False,
        allow_missing=False,  # see the note in `scan`
        config_dir=config_dir,
        diagnostics=diag,
        ts_enabled=ts.enabled,
    )
    resolved_churn_days = _resolved_churn_days(churn_days, cfg)
    # `root=config_dir`, not the process cwd. `analyze` defaults to `Path.cwd()`, so
    # `explain` computed `FunctionId.path` against a different root than every other
    # command: run from a nested package it rejected the very target `check` had just
    # printed, breaking `_discover_config`'s promise that a nested run matches a
    # root-level one. Routing through `_build_report_or_exit` — the same boundary the
    # other four commands use — also gets it the `coverage_map`, `missing_coverage`
    # policy, and TypeScript backend it silently lacked, and turns a missing
    # `[typescript]` extra into exit 2 with the install hint instead of a traceback.
    report = _build_report_or_exit(
        [file_path],
        config_dir=config_dir,
        coverage_path=coverage_path,
        coverage_map=resolved_coverage_map or None,
        include=[],
        exclude=[],
        allow=[],
        use_git=not no_git,
        churn_days=resolved_churn_days,
        cfg=cfg,
        missing_coverage=missing_coverage,
        ts_enabled=ts.enabled,
        ts_coverage=ts.coverage,
        ts_entry=ts.entry,
    )
    _populate_run_diagnostics(
        diag,
        report=report,
        reported_functions=len(report.functions),
        include=[],
        exclude=[],
        allow=[],
        use_git=not no_git,
        churn_days=resolved_churn_days,
        root=config_dir,
    )
    fn = report.find(target)
    if fn is None:
        _exit_target_not_found(target, report)
    redaction = _resolve_redaction(
        redact_paths=redact_paths,
        redact_qualnames=redact_qualnames,
        private_comment=private_comment,
        redact_salt=redact_salt,
        cfg=cfg,
        config_dir=config_dir,
    )
    fn = redact_function(fn, redaction)
    _emit_explanation(
        fn,
        json_output=json_output,
        summary=summary,
        links=_links_for(repo_url, commit_ref, redaction),
    )
    _emit_diagnostics(
        diag,
        verbose=verbose,
        debug_json=debug_json,
        debug_json_file=debug_json_file,
        redaction=redaction,
    )


@app.command()
def diff(
    paths: Annotated[
        list[Path] | None,
        typer.Argument(help=_PATHS_HELP.format(action="diff against baseline")),
    ] = None,
    coverage: CoverageOption = None,
    coverage_map: Annotated[
        list[str] | None,
        typer.Option("--coverage-map", help="Per-prefix coverage path, repeatable."),
    ] = None,
    baseline_path: Annotated[Path | None, typer.Option("--baseline", help="Path to baseline JSON.")] = None,
    config: ConfigOption = None,
    format: FormatOption = "table",
    json_output: Annotated[
        bool, typer.Option("--json", help="Shortcut for --format json. Overrides --format.")
    ] = False,
    output: OutputOption = None,
    summary: Annotated[bool, typer.Option("--summary", help="Emit aggregate summary only.")] = False,
    fail_regression_above: FailRegressionAboveOption = None,
    fail_component_regression_above: FailComponentRegressionAboveOption = None,
    no_component_regression_gate: NoComponentRegressionGateOption = False,
    include: IncludeOption = None,
    exclude: ExcludeOption = None,
    allow: AllowOption = None,
    no_git: NoGitOption = False,
    churn_days: Annotated[
        int | None,
        typer.Option("--churn-days", help="Churn window in days. Default 90."),
    ] = None,
    allow_missing_coverage: Annotated[
        bool,
        typer.Option("--allow-missing-coverage", help="Allow diffing without configured coverage data."),
    ] = False,
    missing_coverage: Annotated[
        str | None,
        typer.Option("--missing-coverage", help="How to handle missing file coverage."),
    ] = None,
    no_auto_cov: Annotated[
        bool,
        typer.Option("--no-auto-cov", help="Skip auto-generating coverage by running the test command."),
    ] = False,
    repo_url: Annotated[
        str | None,
        typer.Option("--repo-url", help="Repository URL for markdown links."),
    ] = None,
    commit_ref: Annotated[
        str | None,
        typer.Option("--commit-ref", help="Commit ref for markdown links."),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", help="Emit structured run diagnostics to stderr (since 0.2.9 P11)."),
    ] = False,
    debug_json: Annotated[
        bool,
        typer.Option("--debug-json", help="Emit diagnostics as a JSON envelope to stderr (since 0.2.9 P11)."),
    ] = False,
    debug_json_file: Annotated[
        Path | None,
        typer.Option("--debug-json-file", help="Write the --debug-json envelope to this file instead."),
    ] = None,
    redact_paths: Annotated[
        bool,
        typer.Option("--redact-paths", help="Hash source paths in output (since 0.2.9 P12)."),
    ] = False,
    redact_qualnames: Annotated[
        bool,
        typer.Option("--redact-qualnames", help="Hash function qualnames in output (since 0.2.9 P12)."),
    ] = False,
    private_comment: Annotated[
        bool,
        typer.Option(
            "--private-comment",
            help="Preset: redact paths + qualnames and suppress source links (since 0.2.9 P12).",
        ),
    ] = False,
    redact_salt: Annotated[
        str | None,
        typer.Option("--redact-salt", help="Salt for redaction hashes (or RISKRATCHET_REDACT_SALT)."),
    ] = None,
    typescript: TypescriptOption = False,
    no_typescript: NoTypescriptOption = False,
    ts_coverage: TsCoverageOption = None,
    ts_entry: TsEntryOption = None,
) -> None:
    """Show full baseline diff; does not fail."""
    cfg, config_dir = _discover_config(config)
    _enforce_config_or_exit(cfg)
    effective_format = _effective_format(format, json_output)
    redaction = _resolve_redaction(
        redact_paths=redact_paths,
        redact_qualnames=redact_qualnames,
        private_comment=private_comment,
        redact_salt=redact_salt,
        cfg=cfg,
        config_dir=config_dir,
    )
    diag = Diagnostics(command="diff")
    baseline_file = baseline_path or _anchor_config_path(
        Path(cfg.get("baseline", ".riskratchet.json")), config_dir
    )
    if not baseline_file.exists():
        typer.secho(
            _format_setup_error(
                f"riskratchet: baseline file not found: {baseline_file}",
                [("Create a baseline of current risk:", "riskratchet baseline")],
            ),
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    old = _load_baseline_or_exit(baseline_file)
    diag.set_baseline(path=str(baseline_file), present=True, entry_count=len(old.entries))
    resolved_paths = _resolved_paths(paths, cfg, config_dir)
    _check_paths_exist(resolved_paths, paths_arg=paths, configured=cfg.get("paths"))
    resolved_include = include or cfg.get("include", [])
    resolved_exclude = exclude or cfg.get("exclude", [])
    resolved_allow = allow or cfg.get("allow", [])
    resolved_churn_days = _resolved_churn_days(churn_days, cfg)
    allow_missing = _resolved_bool(allow_missing_coverage, cfg.get("allow_missing_coverage"))
    ts = _resolve_ts_settings(
        typescript,
        no_typescript,
        False,
        ts_coverage=ts_coverage,
        ts_entry=ts_entry,
        cfg=cfg,
        config_dir=config_dir,
        allow_missing=allow_missing,
        required=True,
    )
    coverage_path, resolved_coverage_map = _resolve_coverage_inputs(
        coverage,
        coverage_map,
        cfg=cfg,
        config_dir=config_dir,
        sources=resolved_paths,
        no_auto_cov=no_auto_cov,
        required=True,
        allow_missing=allow_missing,
        map_allow_missing=allow_missing,
        diag=diag,
        ts=ts,
        include=resolved_include,
        exclude=resolved_exclude,
    )
    _emit_diagnostics_banner(
        command="diff",
        scan_roots=resolved_paths,
        coverage_path=coverage_path,
        config_dir=config_dir,
        coverage_map=resolved_coverage_map,
        redaction=redaction,
    )
    report = _build_report_or_exit(
        resolved_paths,
        config_dir=config_dir,
        coverage_path=coverage_path,
        coverage_map=resolved_coverage_map or None,
        include=resolved_include,
        exclude=resolved_exclude,
        allow=resolved_allow,
        use_git=not no_git,
        churn_days=resolved_churn_days,
        cfg=cfg,
        missing_coverage=missing_coverage,
        ts_enabled=ts.enabled,
        ts_coverage=ts.coverage,
        ts_entry=ts.entry,
    )
    old, report = _apply_ts_identity_guard(
        old,
        report,
        ts_enabled=ts.enabled,
        paths=resolved_paths,
        baseline_file=baseline_file,
        ts_coverage=ts.coverage,
    )
    _populate_run_diagnostics(
        diag,
        report=report,
        reported_functions=len(report.functions),
        include=resolved_include,
        exclude=resolved_exclude,
        allow=resolved_allow,
        use_git=not no_git,
        churn_days=resolved_churn_days,
        root=config_dir,
    )
    _warn_empty_scan(report, command="diff")
    diff_report = diff_baseline(
        report,
        old,
        fail_regression_above=_resolved_float(
            fail_regression_above, cfg.get("fail_regression_above"), default=5.0
        ),
        fail_component_regression_above=_resolved_float(
            fail_component_regression_above,
            cfg.get("fail_component_regression_above"),
            default=15.0,
        ),
        component_regression_gate=(
            not no_component_regression_gate
            and _resolved_bool(True, cfg.get("component_regression_gate"), default=True)
        ),
        groups=_resolved_groups(cfg),
    )
    if redaction.active:
        diff_report = redact_diff(diff_report, redaction)
    links = _links_for(repo_url, commit_ref, redaction)
    if summary:
        rendered = (
            render_diff_summary_json(diff_report)
            if effective_format == "json"
            else render_diff_summary_text(diff_report)
        )
    elif effective_format == "json":
        rendered = render_diff_json(diff_report, links=links)
    elif effective_format == "markdown":
        rendered = render_diff_markdown(diff_report, links=links)
    elif effective_format == "pr-comment":
        rendered = render_diff_pr_comment(diff_report, links=links)
    elif effective_format == "github":
        rendered = render_diff_github(diff_report)
    elif effective_format == "sarif":
        rendered = render_regressions_sarif(
            regressions_from_diff(
                diff_report,
                fail_new_above=_resolved_float(None, cfg.get("fail_new_above"), default=50.0),
                fail_existing_above=_resolved_optional_float(None, cfg.get("fail_existing_above")),
            ),
            links=links,
        )
    else:
        rendered = render_diff_table(diff_report, links=links)
    _write(rendered, output)
    _emit_diagnostics(
        diag,
        verbose=verbose,
        debug_json=debug_json,
        debug_json_file=debug_json_file,
        redaction=redaction,
    )


DOCTOR_SCHEMA_URL = "https://github.com/KayhanB21/riskratchet/schemas/doctor.schema.json"


@app.command("init")
def init_command(
    pyproject: Annotated[
        Path,
        typer.Option("--pyproject", help="Target pyproject.toml. Default: ./pyproject.toml."),
    ] = Path("pyproject.toml"),
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help=f"Replace an existing {_CONFIG_TABLE} block in place.",
        ),
    ] = False,
    no_snippet: Annotated[
        bool,
        typer.Option(
            "--no-snippet",
            help="Skip the CI snippet output (script-friendly).",
        ),
    ] = False,
    with_baseline: Annotated[
        bool | None,
        typer.Option(
            "--with-baseline/--no-baseline",
            help=(
                "Run pytest --cov and create a baseline as part of init. "
                "When unset, prompts interactively if stdin is a TTY and "
                "pytest is detected; otherwise skips."
            ),
        ),
    ] = None,
) -> None:
    """Scaffold `\\[tool.riskratchet]` config + print the CI snippet.

    Idempotent: re-running on a configured project is a no-op unless
    `--force`. Detects the test runner so the suggested test command
    matches your stack. With `--with-baseline` (or an interactive yes
    to the prompt), runs pytest --cov and `baseline` to skip the
    manual two-step that follows.
    """
    outcome = write_starter_config(pyproject, force=force)
    config_dir = pyproject.resolve().parent
    runner = detect_test_runner(config_dir)
    color = {
        InitOutcome.CREATED: typer.colors.GREEN,
        InitOutcome.APPENDED: typer.colors.GREEN,
        InitOutcome.REPLACED: typer.colors.YELLOW,
        InitOutcome.SKIPPED: typer.colors.CYAN,
    }[outcome]
    typer.secho(f"riskratchet init: {outcome.value} [tool.riskratchet] in {pyproject}", fg=color)
    typer.echo(f"detected test runner: {runner.value}")
    if outcome is InitOutcome.SKIPPED:
        typer.echo("(re-run with --force to replace the existing block)")
    if not no_snippet:
        typer.echo("")
        typer.echo(render_ci_snippet())
    if _should_run_baseline(with_baseline=with_baseline, runner=runner):
        _run_baseline_from_init(config_dir)
    else:
        typer.echo("Next:")
        typer.echo("  1. pytest --cov --cov-branch --cov-report=json:coverage.json -q")
        typer.echo("  2. riskratchet baseline src --coverage coverage.json")
        typer.echo("  3. riskratchet check src --coverage coverage.json")


def _should_run_baseline(*, with_baseline: bool | None, runner: RunnerKind) -> bool:
    """Decide whether `init` should run pytest --cov + baseline now.

    Explicit `--with-baseline` / `--no-baseline` wins. Otherwise, only
    prompt when stdin is a TTY *and* pytest is detected: an interactive
    user on a pytest stack is the only scenario where running
    `pytest --cov` blind is likely to succeed.
    """
    import sys

    if with_baseline is not None:
        return with_baseline
    if not sys.stdin.isatty():
        return False
    if runner is not RunnerKind.PYTEST:
        return False
    return typer.confirm(
        "Run pytest --cov and create a baseline now?",
        default=False,
    )


def _run_baseline_from_init(config_dir: Path) -> None:
    """Run pytest --cov + emit a baseline, both anchored to `config_dir`.

    Failures (pytest non-zero, baseline write errors) surface as stderr
    diagnostics and exit 1 — keeping the failure mode of `init` aligned
    with running each step by hand instead of pretending it succeeded.
    """
    import subprocess

    coverage_path = config_dir / "coverage.json"
    typer.echo("")
    typer.secho(
        f"running: pytest --cov --cov-branch --cov-report=json:{coverage_path} -q",
        fg=typer.colors.CYAN,
    )
    result = subprocess.run(
        [
            "pytest",
            "--cov",
            "--cov-branch",
            f"--cov-report=json:{coverage_path}",
            "-q",
        ],
        cwd=config_dir,
        check=False,
    )
    if result.returncode != 0 or not coverage_path.exists():
        typer.secho(
            "pytest --cov did not produce coverage.json; baseline skipped. "
            "Run the three Next: steps manually.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=1)
    typer.secho("running: riskratchet baseline (anchored to config dir)", fg=typer.colors.CYAN)
    # Read the project's own config rather than guessing. `init --with-baseline` runs
    # even when the starter block was SKIPPED — i.e. on an already-configured project —
    # and it used to hardcode `src` and `.riskratchet.json` and pass no `weights`,
    # `include`/`exclude`/`allow`, `groups`, `churn_days`, or coverage policy. On a
    # project scanning `lib` with custom weights that produced a differently-scoped,
    # differently-scored file at the wrong path, and the first `check` then saw mass
    # new/removed entries plus spurious regressions.
    cfg, _ = _discover_config(None)
    settings = resolve_gate_settings(cfg, config_dir)
    scan_paths = settings.paths or [config_dir]
    report = build_report(
        scan_paths,
        root=config_dir,
        coverage_path=coverage_path,
        include=settings.include,
        exclude=settings.exclude,
        allow=settings.allow,
        churn_days=settings.churn_days,
        weights=settings.weights,
        missing_coverage_policy=settings.missing_coverage,
        groups=settings.groups,
    )
    baseline_file = _anchor_config_path(Path(cfg.get("baseline", ".riskratchet.json")), config_dir)
    _refuse_to_erase_baseline(report, baseline_file)
    _save_baseline_or_exit(baseline_from_report(report), baseline_file)
    typer.secho(
        f"wrote baseline with {len(report.functions)} functions to {baseline_file}",
        fg=typer.colors.GREEN,
    )


@app.command()
def doctor(
    config: Annotated[Path | None, typer.Option("--config", help="Path to pyproject.toml.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit the doctor envelope as JSON.")] = False,
) -> None:
    """Diagnose setup: paths, baseline, coverage, git, config, suppressions.

    Some checks are conditional: the coverage-derived ones need a loadable
    coverage file, and the TypeScript identity check only appears when the
    baseline actually records TypeScript entries.

    Exits 0 only when every check is `pass` or `warn`. A single `fail` exits 1
    with the remediation command in the per-check row, so a user can
    copy-paste the fix instead of guessing.
    """
    cfg, config_dir = _discover_config(config)
    # Warn, never exit: `doctor` must not bail out on the very thing it exists to
    # diagnose. A bad value is reported as its own `config` WARN row instead.
    _warn_config_problems(cfg)
    paths = _resolved_paths(None, cfg, config_dir)
    baseline_file = _anchor_config_path(Path(cfg.get("baseline", ".riskratchet.json")), config_dir)
    coverage_path, coverage_origin = _doctor_coverage_source(cfg, config_dir)
    checks = diagnose(
        config_dir=config_dir,
        cfg=cfg,
        paths=paths,
        baseline_file=baseline_file,
        coverage_path=coverage_path,
        coverage_origin=coverage_origin,
    )
    if json_output:
        payload: dict[str, object] = {
            "$schema": DOCTOR_SCHEMA_URL,
            "version": __version__,
            "checks": [_doctor_check_payload(c) for c in checks],
            "summary": summarize(checks),
        }
        typer.echo(json.dumps(payload, indent=2))
    else:
        _emit_doctor_table(checks)
    if any(c.status is CheckStatus.FAIL for c in checks):
        raise typer.Exit(code=1)


def _doctor_coverage_source(cfg: Mapping[str, Any], config_dir: Path) -> tuple[Path | None, str]:
    """Resolve which coverage file `doctor` should inspect, mirroring `check`.

    Reading only `coverage` used to mis-report a project configured purely with
    `coverage_map`, or one relying on `auto_coverage` + `coverage_cache`, as
    "no coverage configured" — riskratchet's own pyproject.toml included.
    """
    auto_on = cfg.get("auto_coverage") is not False
    coverage_value = cfg.get("coverage")
    if isinstance(coverage_value, str):
        # `coverage_auto` means "named in config, but auto-coverage would fill it"
        # — the case `config._report_missing_coverage` treats as a warning.
        return _anchor_config_path(Path(coverage_value), config_dir), (
            "coverage_auto" if auto_on else "coverage"
        )
    coverage_map = cfg.get("coverage_map")
    if isinstance(coverage_map, dict) and coverage_map:
        first = next(iter(coverage_map.values()))
        return _anchor_config_path(Path(str(first)), config_dir), "coverage_map"
    if auto_on:
        cache = cfg.get("coverage_cache", str(DEFAULT_CACHE_PATH))
        return _anchor_config_path(Path(str(cache)), config_dir), "coverage_cache"
    return None, "coverage"


def _doctor_check_payload(check: DoctorCheck) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": check.name,
        "status": check.status.value,
        "summary": check.summary,
    }
    if check.remediation is not None:
        payload["remediation"] = check.remediation
    return payload


def _emit_doctor_table(checks: list[DoctorCheck]) -> None:
    """Plain-text doctor output: status table on stdout, `→ fix:` on stderr."""
    status_glyph = {
        CheckStatus.PASS: typer.style("PASS", fg=typer.colors.GREEN),
        CheckStatus.WARN: typer.style("WARN", fg=typer.colors.YELLOW),
        CheckStatus.FAIL: typer.style("FAIL", fg=typer.colors.RED),
    }
    typer.echo("riskratchet doctor")
    for check in checks:
        typer.echo(f"  {status_glyph[check.status]}  {check.name:<17} {check.summary}")
        if check.status is not CheckStatus.PASS and check.remediation:
            # Remediation on stderr so `2>/dev/null` filters to status only.
            typer.echo(f"        → fix:    {check.remediation}", err=True)
    summary = summarize(checks)
    typer.echo("")
    typer.echo(
        f"riskratchet: {summary['passed']} pass, {summary['warned']} warn, "
        f"{summary['failed']} fail (of {summary['total']})"
    )


def _emit_report(
    report: RiskReport,
    *,
    format: str,
    output: Path | None,
    limit: int,
    quiet: bool = False,
    min_score: float | None = None,
    links: SourceLinks | None = None,
    summary: bool = False,
) -> None:
    effective_limit = None if limit == 0 else limit
    if summary:
        rendered = (
            render_report_summary_json(report) if format == "json" else render_report_summary_text(report)
        )
    elif format == "json":
        rendered = render_report_json(report, links=links)
    elif format == "markdown":
        rendered = render_report_markdown(report, limit=effective_limit, links=links)
    elif format == "sarif":
        rendered = render_report_sarif(
            report,
            min_score=min_score if min_score is not None else 25.0,
            links=links,
        )
    elif format == "github":
        rendered = render_report_github(report, min_score=min_score if min_score is not None else 25.0)
    elif format == "pr-comment":
        rendered = render_report_pr_comment(report, limit=effective_limit, links=links)
    else:
        rendered = render_report_table(report, limit=effective_limit, include_summary=not quiet, links=links)
    _write(rendered, output)


def _effective_format(format: str, json_output: bool) -> str:
    if json_output:
        return "json"
    _validate_format(format)
    return format


def _emit_scan_next_step_footer(report: RiskReport, *, baseline_file: Path, config_present: bool) -> None:
    """Stdout footer suggesting `init`, `baseline`, or `--fail-above`.

    Fires only when the user has no baseline configured. Adapts to two
    axes: whether `[tool.riskratchet]` is present (otherwise lead with
    `riskratchet init`), and whether the scan turned up anything above
    medium severity (otherwise say "nothing to baseline yet").
    """
    if baseline_file.exists():
        return
    risky = sum(1 for fn in report.functions if severity(fn.score) is not Severity.LOW)
    typer.echo("")
    if risky:
        bullets: list[str] = []
        if not config_present:
            bullets.append("  - configure first:                    riskratchet init")
        bullets.append("  - lock in this state as a baseline:   riskratchet baseline")
        bullets.append("  - gate on absolute threshold instead: riskratchet check --fail-above 60")
        typer.echo(
            f"riskratchet: {risky} function(s) at severity medium or higher. Next:\n" + "\n".join(bullets)
        )
    elif not config_present:
        typer.echo(
            "riskratchet: 0 functions at severity medium or higher — "
            "run `riskratchet init` to set up, then revisit."
        )
    else:
        typer.echo("riskratchet: 0 functions at severity medium or higher — nothing to baseline yet.")


def _emit_regression_hint(regressions: list[Regression], *, baseline_file: Path) -> None:
    """Print escape-hatch hints to stderr when `check` exits with regressions.

    Stays on stderr so `--json` consumers still see a clean stdout payload.
    """
    typer.secho("", err=True)
    typer.secho("riskratchet: regressions detected. Options:", fg=typer.colors.YELLOW, err=True)
    if any(r.kind is RegressionKind.NEW_ABOVE_THRESHOLD for r in regressions):
        typer.secho(
            "  Note: 'new' means absent from the baseline, not necessarily changed in this commit.",
            err=True,
        )
    typer.secho(
        f"  1. Accept the new state as the baseline (if the change is intentional):\n"
        f"       riskratchet baseline <paths> --coverage <coverage.json> --output {baseline_file}",
        err=True,
    )
    has_component = any(r.kind is RegressionKind.COMPONENT_REGRESSED for r in regressions)
    if has_component:
        typer.secho(
            "  2. Loosen or disable the per-component gate (this run only):\n"
            "       riskratchet check ... --no-component-regression-gate\n"
            "       riskratchet check ... --fail-component-regression-above 25\n"
            "     Or persist via [tool.riskratchet] component_regression_gate / "
            "fail_component_regression_above in pyproject.toml.",
            err=True,
        )
    typer.secho(
        "  Tip: option 1 keeps the ratchet honest; option 2 is for one-off triage.",
        fg=typer.colors.CYAN,
        err=True,
    )


def _emit_above_threshold_hint(regressions: list[Regression], *, threshold: float) -> None:
    """Stderr hint shown when `check --fail-above N` (no-baseline) gates.

    Different remediation menu than the baseline path: there is no baseline
    to regenerate, so the options are to fix the function, loosen the
    threshold, or adopt a baseline going forward.
    """
    typer.secho("", err=True)
    typer.secho(
        f"riskratchet: {len(regressions)} function(s) scored above --fail-above {threshold:.1f}. Options:",
        fg=typer.colors.YELLOW,
        err=True,
    )
    typer.secho(
        "  1. Reduce risk in the listed functions (extract helpers, add tests, etc.).",
        err=True,
    )
    typer.secho(
        f"  2. Raise the threshold (this run only): --fail-above {min(threshold + 5.0, 100.0):.0f}",
        err=True,
    )
    typer.secho(
        "  3. Adopt a baseline so only future regressions fail:\n"
        "       riskratchet baseline <paths> --coverage <coverage.json>",
        err=True,
    )
    typer.secho(
        "  Tip: --fail-above is for the no-baseline 'try it on a public repo' use case; "
        "for steady-state CI prefer option 3.",
        fg=typer.colors.CYAN,
        err=True,
    )


def _render_regressions(
    regressions: list[Regression],
    *,
    format: str,
    links: SourceLinks | None = None,
) -> str:
    if format == "json":
        return render_regressions_json(regressions, links=links)
    if format == "markdown":
        return render_regressions_markdown(regressions, links=links)
    if format == "pr-comment":
        return render_regressions_pr_comment(regressions, links=links)
    if format == "github":
        return render_regressions_github(regressions)
    if format == "sarif":
        return render_regressions_sarif(regressions, links=links)
    return render_regressions_table(regressions, links=links)


def _write(rendered: str, output: Path | None) -> None:
    if output is None:
        typer.echo(rendered, nl=False)
        return
    _write_or_exit(output, rendered, what="report")


def _write_or_exit(destination: Path, payload: str, *, what: str) -> None:
    """Write `payload` to `destination`, or exit 2 explaining why it could not be written.

    Unguarded, `--output` at a directory or under a read-only path escaped as an
    `IsADirectoryError`/`PermissionError` traceback and **exit 1** — the code reserved
    for "a gate tripped", from a command whose own docstring says it never fails. Every
    other I/O boundary here (`_load_baseline_or_exit`, `_build_report_or_exit`) already
    converts to a setup error, so this one does too.
    """
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(payload, encoding="utf-8")
    except OSError as exc:
        _exit_unwritable(destination, exc, what=what)


def _exit_unwritable(destination: Path, exc: OSError, *, what: str) -> NoReturn:
    """Report an unwritable output path as a setup error and exit 2."""
    typer.secho(
        _format_setup_error(
            f"riskratchet: could not write {what} to {destination}: {exc}.",
            [
                ("Point it at a writable file:", f"<command> --output path/to/{destination.name}"),
                ("Or drop the flag to write to stdout:", "<command>"),
            ],
        ),
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(code=2) from exc


def _save_baseline_or_exit(baseline: Baseline, destination: Path) -> None:
    """Persist the baseline, or exit 2 rather than tracebacking out with exit 1."""
    try:
        save_baseline(baseline, destination)
    except OSError as exc:
        _exit_unwritable(destination, exc, what="baseline")


def _emit_explanation(
    fn: FunctionRisk,
    *,
    json_output: bool,
    summary: bool,
    links: Any,
) -> None:
    """Render one function in whichever of `explain`'s four output shapes was asked for."""
    if summary and json_output:
        typer.echo(render_function_summary_json(fn), nl=False)
    elif json_output:
        typer.echo(render_function_json(fn, links=links), nl=False)
    elif summary:
        # Text summary: severity/score one-liner.
        typer.echo(
            f"{fn.id.as_target()}  severity={severity(fn.score).value}  "
            f"score={fn.score:.1f}  crap={fn.crap:.1f}"
        )
    else:
        typer.echo(render_function_explanation(fn), nl=False)


def _exit_target_not_found(target: str, report: RiskReport) -> NoReturn:
    """Report an unresolvable `explain` target, naming the canonical spelling.

    Targets are repo-relative, because that is the form `check` and `diff` print. A
    cwd-relative spelling used to resolve here and nowhere else, so pointing at the
    real target is more useful than repeating that it was not found.
    """
    _, _, qualname = target.partition("::")
    near = [fn.id.as_target() for fn in report.functions if fn.id.qualname == qualname]
    fixes = [("Use the target riskratchet prints:", near[0])] if near else []
    typer.secho(
        _format_setup_error(f"riskratchet: function not found: {target}.", fixes)
        if fixes
        else f"function not found: {target}",
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(code=2)


@dataclass(frozen=True)
class _TsSettings:
    """The resolved TypeScript switch and its two path lists, flag → config → off."""

    enabled: bool
    coverage: list[Path]
    entry: list[Path]


def _resolve_typescript_flag(
    typescript: bool,
    no_typescript: bool,
    experimental_typescript: bool,
    *,
    cfg: dict[str, Any],
) -> bool:
    """Resolve the TypeScript toggle: `--no-typescript`, `--typescript`, the alias, then config.

    `--no-typescript` is an explicit off and beats both the alias and
    `[tool.riskratchet] typescript = true`: a switch config can turn on must be one a
    flag can turn back off, or a real-valued default could never lose to config —
    the pytest-plugin lesson from 0.3.5, applied before it repeats. Two flags rather
    than one `--typescript/--no-typescript` pair because Typer renders the pair as two
    sub-columns, which squeezes every option's help text at 80 columns.
    """
    if no_typescript:
        return False
    if typescript:
        return True
    if experimental_typescript:
        typer.secho(
            "--experimental-typescript is deprecated; use --typescript (TypeScript is scored since 0.3.0).",
            fg=typer.colors.YELLOW,
            err=True,
        )
        return True
    return resolved_typescript(None, cfg)


def _resolve_ts_settings(
    typescript: bool,
    no_typescript: bool,
    experimental_typescript: bool,
    *,
    ts_coverage: list[Path] | None,
    ts_entry: list[Path] | None,
    cfg: dict[str, Any],
    config_dir: Path,
    allow_missing: bool,
    required: bool,
) -> _TsSettings:
    """Resolve the switch and the `--ts-coverage` / `--ts-entry` lists against config.

    Warns when reports or entries were named (by flag or by key) for a run that will not
    analyze TypeScript, since they would otherwise be silently ignored. The coverage list
    comes back already checked by `_ensure_ts_coverage_exists`, so a report the guard
    tolerated never reaches the strict loader.
    """
    enabled = _resolve_typescript_flag(typescript, no_typescript, experimental_typescript, cfg=cfg)
    coverage = resolved_ts_paths(ts_coverage, cfg, "ts_coverage", config_dir)
    entry = resolved_ts_paths(ts_entry, cfg, "ts_entry", config_dir)
    if (coverage or entry) and not enabled:
        typer.secho(
            "typescript: --ts-coverage / --ts-entry have no effect without --typescript "
            "(or `typescript = true` in [tool.riskratchet]); the same goes for the ts_coverage / "
            "ts_entry keys.",
            fg=typer.colors.YELLOW,
            err=True,
        )
    if not enabled:
        # Inert config keys stay inert: a `--no-typescript` run must not exit 2 on a
        # TypeScript report it will never read. A report named on the command line keeps
        # its contract — the user asserted it exists for this run.
        coverage = list(ts_coverage or [])
        entry = list(ts_entry or [])
    coverage = _ensure_ts_coverage_exists(
        coverage, allow_missing=allow_missing, required=required, from_config=not ts_coverage
    )
    return _TsSettings(enabled=enabled, coverage=coverage, entry=entry)


def _resolve_coverage_inputs(
    coverage: Path | None,
    coverage_map: list[str] | None,
    *,
    cfg: dict[str, Any],
    config_dir: Path,
    sources: list[Path],
    no_auto_cov: bool,
    required: bool,
    allow_missing: bool,
    map_allow_missing: bool,
    diag: Diagnostics,
    ts: _TsSettings,
    include: list[str],
    exclude: list[str],
) -> tuple[Path | None, dict[str, Path]]:
    """Resolve the Python coverage source for a command: one file, or a per-prefix map.

    `scan`, `baseline`, `check`, and `diff` inlined this identical block; one boundary
    means the 0.3.6 rule — Python coverage is not applicable on a tree with no Python
    under the scan paths — is applied in all four at once rather than remembered per
    command. Returns `(coverage_path, coverage_map)`; at most one of them is set.
    """
    resolved_map = _resolved_coverage_map(coverage_map, cfg, config_dir)
    if resolved_map:
        _ensure_coverage_map_exists(resolved_map, allow_missing=map_allow_missing)
        diag.set_coverage(
            mode="map",
            source="map",
            coverage_map={prefix: str(path) for prefix, path in resolved_map.items()},
        )
        return None, resolved_map
    coverage_path = _resolve_coverage(
        coverage,
        cfg,
        sources=sources,
        no_auto_cov=no_auto_cov,
        required=required,
        allow_missing=allow_missing,
        config_dir=config_dir,
        diagnostics=diag,
        ts_enabled=ts.enabled,
        include=include,
        exclude=exclude,
    )
    return coverage_path, resolved_map


def _ts_warn(message: str) -> None:
    typer.secho(f"typescript: {message}", fg=typer.colors.YELLOW, err=True)


def _ts_rebaseline_command(
    paths: list[Path],
    *,
    baseline_file: Path,
    ts_coverage: list[Path] | None,
) -> str:
    """The exact `riskratchet baseline` invocation that regenerates a stale-TS baseline.

    Reuses the scanned paths, `--typescript`, any `--ts-coverage` reports from this
    run, and `--output <baseline_file>` so the printed line is copy-pasteable as-is.
    """
    paths_str = " ".join(str(path) for path in paths) or "<paths>"
    command = f"riskratchet baseline {paths_str} --typescript"
    for coverage in ts_coverage or []:
        command += f" --ts-coverage {coverage}"
    command += f" --output {baseline_file}"
    return command


def _warn_unratcheted_languages(old: Baseline, report: RiskReport) -> None:
    """Say when the baseline holds a language this run did not analyze.

    The read-side twin of `_refuse_to_drop_a_language`. Those entries simply vanish from
    the comparison — `compare` has no "removed" concept — so a `check` without
    `--typescript` over a mixed baseline gated only the Python half and reported a clean
    run. `doctor` already knows how to say this; the gate did not.
    """
    for language, lost in languages_not_scanned(old, report).items():
        hint = (
            " (pass --typescript, or set typescript = true in [tool.riskratchet])"
            if language == "typescript"
            else ""
        )
        typer.secho(
            f"warning: the baseline holds {_count(lost, f'{language} entry', f'{language} entries')} "
            f"but this run analyzed no {language} — those functions are not being gated{hint}.",
            fg=typer.colors.YELLOW,
            err=True,
        )


def _apply_ts_identity_guard(
    old: Baseline,
    report: RiskReport,
    *,
    ts_enabled: bool,
    paths: list[Path],
    baseline_file: Path,
    ts_coverage: list[Path] | None = None,
) -> tuple[Baseline, RiskReport]:
    """When TS is analyzed against a baseline whose recorded TS grammar/scheme differs from the
    runtime's, the persisted TS fingerprints are stale — match TypeScript by id only (never by a
    cross-grammar fingerprint) and tell the user to re-baseline. Python matching is unaffected.

    Beyond warning, print the exact `riskratchet baseline ... --output <baseline_file>` command so
    an adopter who bumped `tree-sitter-typescript` can re-baseline in one paste. Stderr-only, so a
    `--json` stdout stays clean."""
    _warn_unratcheted_languages(old, report)
    if not ts_enabled or not typescript_identity_stale(old):
        return old, report
    _ts_warn(
        "baseline TypeScript grammar/scheme differs from the runtime; matching TypeScript functions "
        "by id only (a grammar bump changes every fingerprint) — re-baseline recommended"
    )
    typer.secho(
        "  re-baseline: "
        + _ts_rebaseline_command(paths, baseline_file=baseline_file, ts_coverage=ts_coverage),
        err=True,
    )
    return suppress_stale_typescript_renames(old, report)


def _rel_or_str(path: Any, root: Path) -> str:
    from ._paths import relative_posix

    try:
        return relative_posix(Path(path), root)
    except (ValueError, OSError):
        return str(path)


def _validate_format(format: str) -> None:
    if format not in VALID_FORMATS:
        raise typer.BadParameter(f"format must be one of {', '.join(VALID_FORMATS)}")


def _load_baseline_or_exit(baseline_file: Path) -> Baseline:
    """Load a baseline, converting parse failures into actionable stderr.

    `load_baseline` raises `ValueError` on a malformed file (junk JSON,
    truncated write, a version this build cannot read, etc.); rather than dump
    that traceback on the user, re-emit it as a remediation-form setup error
    pointing at the next command to run.

    Individually unreadable entries are survivable, so they warn instead of
    exiting — but they must not pass in silence, because a dropped entry means
    that function is no longer ratcheted.
    """
    try:
        return load_baseline(baseline_file, on_dropped=_warn_dropped_baseline_entries)
    except ValueError as exc:
        # A baseline from a *newer* riskratchet is fixed by upgrading; offering
        # "regenerate" first would talk the user into overwriting a good file.
        fixes = [("Regenerate the baseline from current risk:", "riskratchet baseline")]
        if isinstance(exc, BaselineVersionError):
            fixes.insert(
                0, ("Upgrade riskratchet to a build that reads it:", "pip install --upgrade riskratchet")
            )
        typer.secho(
            _format_setup_error(f"riskratchet: cannot read baseline {baseline_file}: {exc}", fixes),
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2) from exc


def _build_report_or_exit(
    resolved_paths: list[Path],
    *,
    config_dir: Path,
    coverage_path: Path | None,
    coverage_map: Mapping[str, Path] | None,
    include: list[str],
    exclude: list[str],
    allow: list[str],
    use_git: bool,
    churn_days: int,
    cfg: dict[str, Any],
    missing_coverage: str | None,
    ts_enabled: bool,
    ts_coverage: list[Path] | None,
    ts_entry: list[Path] | None,
) -> RiskReport:
    """Build a report for `scan`/`check`/`diff`/`baseline`, converting setup
    failures into actionable stderr.

    The four commands used to inline this identical `build_report` call plus a
    lone `except ImportError`, which meant an unreadable coverage file escaped
    as a raw `ValueError` traceback (`engine.analyze` -> `load_coverage`). One
    boundary keeps the error handling honest in all four places at once, and
    mirrors `_load_baseline_or_exit`: a bad file is a setup problem, so it
    exits 2 with the command to run next, never a traceback.
    """
    if use_git and is_shallow_repo(config_dir):
        typer.secho(
            "riskratchet: shallow clone detected; churn signals score as zero. "
            "Use actions/checkout with 'fetch-depth: 0' (or run 'git fetch --unshallow').",
            fg=typer.colors.YELLOW,
            err=True,
        )
    try:
        return _warned_about_inert_allow(
            build_report(
                resolved_paths,
                root=config_dir,
                coverage_path=coverage_path,
                coverage_map=coverage_map,
                include=include,
                exclude=exclude,
                allow=allow,
                use_git=use_git,
                churn_days=churn_days,
                weights=_resolved_weights(cfg),
                missing_coverage_policy=_resolved_missing_coverage(missing_coverage, cfg),
                groups=_resolved_groups(cfg),
                typescript=ts_enabled,
                ts_coverage_paths=ts_coverage or [],
                ts_entries=ts_entry or [],
                on_ts_warning=_ts_warn,
                on_ts_error=lambda path, msg: _ts_warn(f"skipping {_rel_or_str(path, config_dir)}: {msg}"),
                on_coverage_error=_coverage_shard_warn,
            ),
            allow,
        )
    except ImportError as exc:  # missing [typescript] extra, surfaced during TS discovery
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    except (FileNotFoundError, ValueError) as exc:
        # Unreadable/malformed coverage, or mutually exclusive coverage flags.
        # `exc` already names the offending path, so it stands as the headline.
        typer.secho(
            _format_setup_error(
                f"riskratchet: {exc}",
                [
                    (
                        "Regenerate the coverage report:",
                        f"pytest --cov --cov-branch --cov-report=json:{coverage_path or 'coverage.json'} -q",
                    ),
                    (
                        "Or run without coverage (every function scores as uncovered):",
                        "riskratchet scan --no-auto-cov",
                    ),
                ],
            ),
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2) from exc


def _warned_about_inert_allow(report: RiskReport, allow: list[str]) -> RiskReport:
    """Say so when `allow` patterns are configured but suppressed nothing.

    A suppression that suppresses nothing is worse than none: `allow` also removes
    entries from the baseline, so the user believes that debt is parked while it is
    still being ratcheted — or, the other way round, believes a function is gated when
    the pattern silently swallowed it. The commonest cause was a pattern in canonical
    `path::qualname` form, which matched nothing at all before 0.3.5.
    """
    if allow and not report.suppressed_functions:
        typer.secho(
            f"warning: {_count(len(allow), 'allow pattern')} configured but nothing was suppressed. "
            "Patterns match `path::qualname`, a path glob, or a qualname — check the spelling "
            "against a target riskratchet prints.",
            fg=typer.colors.YELLOW,
            err=True,
        )
    return report


def _warn_dropped_baseline_entries(count: int) -> None:
    """Report baseline entries that were present but unreadable.

    The file itself parsed, so the run continues — but a dropped entry silently
    leaves that function unratcheted, which is exactly the kind of quiet gap a
    ratchet must never keep to itself.
    """
    typer.secho(
        f"riskratchet: baseline: skipped {_count(count, 'malformed entry', 'malformed entries')}; "
        "those functions are not "
        "ratcheted. Run 'riskratchet baseline' to regenerate.",
        fg=typer.colors.YELLOW,
        err=True,
    )


def _coverage_shard_warn(path: Path, message: str) -> None:
    """Report a `--coverage-map` shard that could not be loaded.

    `_ensure_coverage_map_exists` promises "treating as no coverage" for a
    missing shard, but the loader used to raise anyway. The warning now fires
    where the shard is actually dropped, so a missing *and* a malformed shard
    get the same one message with the same remediation.
    """
    typer.secho(
        _format_setup_error(
            f"riskratchet: coverage-map shard unusable: {path} ({message}); "
            "treating that prefix as no coverage.",
            [
                (
                    "Generate coverage at this path:",
                    f"pytest --cov --cov-branch --cov-report=json:{path} -q",
                ),
            ],
        ),
        fg=typer.colors.YELLOW,
        err=True,
    )


def _warn_unknown_keys(cfg: Mapping[str, Any]) -> None:
    """One yellow line naming `[tool.riskratchet]` keys this build ignores.

    Always a warning, never fatal: an unknown key may simply come from a
    config written for a newer riskratchet, and refusing to run would make
    upgrading riskratchet the only way to downgrade it.
    """
    unknown = unknown_config_keys(cfg)
    if unknown:
        typer.secho(
            f"warning: ignoring unknown [tool.riskratchet] key(s): {', '.join(unknown)}",
            fg=typer.colors.YELLOW,
            err=True,
        )


def _warn_config_problems(cfg: Mapping[str, Any]) -> None:
    """Report both classes of config problem without changing the exit code."""
    _warn_unknown_keys(cfg)
    for problem in invalid_config_values(cfg):
        typer.secho(f"warning: [tool.riskratchet] {problem}", fg=typer.colors.YELLOW, err=True)


def _enforce_config_or_exit(cfg: Mapping[str, Any]) -> None:
    """Warn on unknown keys; exit 2 on a known key this build cannot use.

    The asymmetry is the point. An unknown key is forward-compatible, but a
    known key with a wrong-typed value can never become right: `fail_new_above
    = "50"` was silently discarded and the *default* 50 applied, so a repo that
    thought it had tightened its gate had not. `weights`, `groups`,
    `missing_coverage`, and `churn_window_days` already exited 2 here; this
    extends the same treatment to the keys that were quietly dropped.

    Every bad value is reported at once — fixing a config with two typos should
    not take two runs.
    """
    _warn_unknown_keys(cfg)
    problems = invalid_config_values(cfg)
    if not problems:
        return
    detail = "\n".join(f"  - {problem}" for problem in problems)
    typer.secho(
        _format_setup_error(
            f"riskratchet: unusable [tool.riskratchet] value(s):\n{detail}",
            [
                ("Check the whole config, with the offending line:", "riskratchet config validate"),
                ("See every supported key and its type:", "riskratchet config show"),
            ],
        ),
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(code=2)


def _count(n: int, singular: str, plural: str | None = None) -> str:
    """Render a count with a correctly pluralized noun: "1 entry", "3 entries"."""
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def _empty_scan_cause(report: RiskReport) -> tuple[str, list[tuple[str, str]]] | None:
    """Describe why a scan produced nothing to gate, or `None` if it did.

    The two causes need different remediations, and `RiskReport` already
    distinguishes them: `analyzed_functions` counts what discovery found,
    `functions` what survived the `allow` patterns (`engine.py`).
    """
    if report.functions:
        return None
    if report.analyzed_functions:
        return (
            f"{_count(report.analyzed_functions, 'function')} found, all suppressed by an allow pattern",
            [
                ("Narrow the suppressions:", '[tool.riskratchet] allow = ["src/legacy/**"]'),
                ("List the patterns in effect:", "riskratchet doctor"),
            ],
        )
    return (
        "no functions were found to analyze",
        [
            ("Point at the package root:", "<command> src/"),
            ("Widen the filters:", "[tool.riskratchet] exclude / include"),
            ("See what would be scanned:", "riskratchet doctor"),
        ],
    )


def _warn_empty_scan(report: RiskReport, *, command: str) -> None:
    """Say that a scan found nothing, without changing the exit code.

    For the inspection commands and for the degenerate zero-functions-against-an-
    empty-baseline case, where there is no gate to protect: a monorepo sweep over
    a package that is legitimately empty must not start failing.
    """
    cause = _empty_scan_cause(report)
    if cause is not None:
        typer.secho(f"riskratchet: {command}: {cause[0]}.", fg=typer.colors.YELLOW, err=True)


def _require_gateable_functions(report: RiskReport, *, command: str, baseline_entries: int) -> None:
    """Exit 2 when nothing was scanned but the baseline has entries to protect.

    A scan yielding zero functions used to report "No risk regressions detected"
    and exit 0, so a typo'd `paths`, a `src/`->`lib/` restructure, or an
    over-broad `exclude` switched the ratchet off and passed green forever. A
    *nonexistent* path was already exit 2 (`_check_paths_exist`); an existing one
    matching nothing was not.

    Zero functions plus a populated baseline cannot arise from a working config,
    which is what makes this safe to fail hard on: a legitimate subset run
    (`riskratchet check src/onepackage`) still yields functions.
    """
    cause = _empty_scan_cause(report)
    if cause is None:
        return
    if not baseline_entries:
        _warn_empty_scan(report, command=command)
        return
    headline, fixes = cause
    typer.secho(
        _format_setup_error(
            f"riskratchet: {command}: {headline}, but the baseline has "
            f"{_count(baseline_entries, 'entry', 'entries')} to protect — the ratchet would check nothing",
            fixes,
        ),
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(code=2)


def _refuse_to_erase_baseline(report: RiskReport, target: Path) -> None:
    """Never replace a populated baseline with an empty one.

    The destructive twin of the `check` hole above: the same misconfiguration
    that made `check` pass silently made `baseline` overwrite a good 400-entry
    file with nothing, discarding the ratchet outright. Writing a *new*
    zero-function baseline is still fine.
    """
    if not target.exists():
        return
    try:
        old = load_baseline(target)
    except (OSError, ValueError):
        return  # Unreadable already; `_load_baseline_or_exit` owns that error.
    if _refuse_to_drop_a_language(report, old, target):
        return
    if report.functions:
        return
    existing = len(old.entries)
    if not existing:
        return
    headline, fixes = _empty_scan_cause(report) or ("", [])
    typer.secho(
        _format_setup_error(
            f"riskratchet: baseline: {headline}; refusing to overwrite {target} "
            f"and discard its {_count(existing, 'entry', 'entries')}",
            fixes,
        ),
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(code=2)


def _refuse_to_drop_a_language(report: RiskReport, old: Baseline, target: Path) -> bool:
    """Never let a run erase every entry of a language it did not analyze.

    `_refuse_to_erase_baseline` only looked at whether the *whole* report was empty, so
    `riskratchet baseline` without `--typescript` over a mixed baseline sailed past it:
    the Python half kept it non-empty while every TypeScript entry was silently dropped,
    and it reported "wrote baseline with 5 functions". A gap in 0.3.4's own fix — the
    unit that must not vanish is a language, not the file.

    A report with *no* functions at all is the plain empty-scan case, which
    `_refuse_to_erase_baseline` already reports with a better message; this only fires
    when one language was analyzed and another silently was not.

    Returns False when there is nothing to refuse; otherwise it exits and never returns.
    """
    if not report.functions:
        return False
    scanned = {fn.language for fn in report.functions}
    baselined = {entry.language for entry in old.entries.values()}
    dropped = sorted(lang for lang in baselined - scanned if lang)
    if not dropped:
        return False
    for language in dropped:
        lost = sum(1 for entry in old.entries.values() if entry.language == language)
        # `--typescript` opts a language in; Python is on by default, so the fix there
        # is to scan the paths that hold it rather than to pass a flag.
        include_it = (
            "riskratchet baseline --typescript"
            if language == "typescript"
            else "riskratchet baseline <paths containing them>"
        )
        typer.secho(
            _format_setup_error(
                f"riskratchet: baseline: this run analyzed no {language} functions, but {target} "
                f"holds {_count(lost, f'{language} entry', f'{language} entries')} — "
                "writing it would drop them and unratchet that half of the repo",
                [
                    ("Analyze it too:", include_it),
                    ("Or write to a separate file:", "riskratchet baseline --output <other.json>"),
                ],
            ),
            fg=typer.colors.RED,
            err=True,
        )
    raise typer.Exit(code=2)


def _check_paths_exist(
    resolved: list[Path],
    *,
    paths_arg: list[Path] | None,
    configured: object,
) -> None:
    """Exit with an actionable error when any scan path is missing.

    Skipped when the resolution defaulted to cwd (no CLI arg and no
    `[tool.riskratchet] paths`) — that case can't be "missing". Splitting
    this out of `config._resolved_paths` keeps `config.py` a pure
    resolver and concentrates the typer.Exit boundary in `cli.py`.
    """
    if not paths_arg and not (isinstance(configured, list) and configured):
        return
    missing = [p for p in resolved if not p.exists()]
    if not missing:
        return
    shown = [str(p) for p in missing]
    if paths_arg:
        headline_origin = "scan paths from CLI arguments do not exist"
        raw: list[Any] | None = None
    else:
        headline_origin = "scan paths from [tool.riskratchet] paths do not exist"
        raw = list(configured) if isinstance(configured, list) else None
    fixes: list[tuple[str, str]] = [
        ("Check the path spelling and rerun:", f"<command> {' '.join(shown)}"),
        ("List a different path:", "<command> src/"),
    ]
    if raw:
        fixes.append(
            (
                "Edit pyproject.toml `[tool.riskratchet] paths`:",
                f"paths = {raw!r}",
            )
        )
    typer.secho(
        _format_setup_error(
            f"riskratchet: {headline_origin}: {', '.join(shown)}",
            fixes,
        ),
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(code=2)


def _validate_baseline_format(format: str) -> None:
    if format not in VALID_BASELINE_FORMATS:
        typer.secho(
            f"unsupported baseline format: {format}. Supported values: {', '.join(VALID_BASELINE_FORMATS)}.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)


def _filtered_report(report: RiskReport, *, min_score: float | None, top: int | None) -> RiskReport:
    functions = sorted(report.functions, key=lambda fn: (-fn.score, fn.id.as_target()))
    if min_score is not None:
        functions = [fn for fn in functions if fn.score >= min_score]
    if top is not None:
        functions = functions[:top]
    return RiskReport(
        functions=tuple(functions),
        files=report.files,
        coverage_status=report.coverage_status,
        suppressed_functions=report.suppressed_functions,
        skipped_missing_coverage=report.skipped_missing_coverage,
        analyzed_functions=report.analyzed_functions or len(report.functions),
    )


def _exit_for_scan_gate(
    report: RiskReport,
    *,
    fail_above: float | None,
    fail_severity: str | None,
) -> None:
    if fail_severity is not None and fail_severity not in VALID_FAIL_SEVERITIES:
        typer.secho(
            f"fail severity must be one of {', '.join(VALID_FAIL_SEVERITIES)}.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    if fail_above is not None and any(fn.score > fail_above for fn in report.functions):
        raise typer.Exit(code=1)
    if fail_severity is not None:
        order = {name: idx for idx, name in enumerate(VALID_FAIL_SEVERITIES)}
        threshold = order[fail_severity]
        if any(order[severity(fn.score).value] >= threshold for fn in report.functions):
            raise typer.Exit(code=1)


def _links_for(
    repo_url: str | None,
    commit_ref: str | None,
    redaction: RedactionConfig,
) -> SourceLinks | None:
    """Resolve source links, suppressed when redaction would break the URLs."""
    if redaction.drop_links:
        return None
    return _resolve_source_links(repo_url, commit_ref)


def _resolve_source_links(repo_url: str | None, commit_ref: str | None) -> SourceLinks | None:
    resolved_repo = repo_url
    if resolved_repo is None:
        server = _env("GITHUB_SERVER_URL")
        repo = _env("GITHUB_REPOSITORY")
        if server is not None and repo is not None:
            resolved_repo = f"{server.rstrip('/')}/{repo.lstrip('/')}"
    resolved_ref = commit_ref or _env("GITHUB_SHA")
    if resolved_repo is None or resolved_ref is None:
        return None
    return SourceLinks(repo_url=resolved_repo, commit_ref=resolved_ref)


def _env(name: str) -> str | None:
    import os

    value = os.environ.get(name)
    return value or None


def _emit_diagnostics(
    diag: Diagnostics,
    *,
    verbose: bool,
    debug_json: bool,
    debug_json_file: Path | None,
    redaction: RedactionConfig,
) -> None:
    """Render `--verbose` lines and/or the `--debug-json` envelope to stderr/file.

    Stdout is never touched here: verbose lines and the bare `--debug-json`
    envelope go to stderr; `--debug-json PATH` writes the envelope to a file.
    Paths in the diagnostics are redacted first so a `--private-comment` run
    does not leak through this surface.
    """
    diag = redact_diagnostics(diag, redaction)
    if verbose:
        for line in diag.to_lines():
            typer.secho(line, err=True)
    if debug_json or debug_json_file is not None:
        try:
            payload = write_debug_json(diag, debug_json_file)
        except OSError as exc:  # an unwritable --debug-json-file is a setup error, not exit 1
            _exit_unwritable(debug_json_file or Path("-"), exc, what="debug JSON")
        if payload is not None:
            typer.echo(payload, err=True)


def _populate_run_diagnostics(
    diag: Diagnostics,
    *,
    report: RiskReport,
    reported_functions: int,
    include: list[str],
    exclude: list[str],
    allow: list[str],
    use_git: bool,
    churn_days: int,
    root: Path,
) -> None:
    """Fill the git / filters / analysis categories from post-run data."""
    diag.set_git(
        enabled=use_git,
        churn_window_days=churn_days,
        repo_present=(root / ".git").exists(),
    )
    diag.set_filters(
        include=include,
        exclude=exclude,
        allow=allow,
        suppressed_functions=report.suppressed_functions,
    )
    diag.set_analysis(
        coverage_status=report.coverage_status,
        analyzed_functions=report.analyzed_functions or len(report.functions),
        reported_functions=reported_functions,
        skipped_missing_coverage=report.skipped_missing_coverage,
    )


def _emit_diagnostics_banner(
    *,
    command: str,
    scan_roots: list[Path],
    coverage_path: Path | None,
    config_dir: Path,
    coverage_map: Mapping[str, Path] | None = None,
    redaction: RedactionConfig | None = None,
) -> None:
    """Print a single 'resolved root + coverage source' line to stderr.

    Always-on so monorepo users can see which package is being scanned with
    which coverage file. `root` is the discovered config directory (which
    equals the current directory unless config was found in an ancestor).
    Stdout stays payload-only. When path redaction is active the path-like
    fields are hashed so this always-on line does not leak under
    `--private-comment`.

    This one-liner is emitted *before* analysis (so a slow or failing run still
    shows what was being scanned); `--verbose` adds a detailed post-analysis
    block via `_emit_diagnostics`. The small coverage-source overlap between the
    two is intentional layering, not duplication.
    """
    cfg = redaction or RedactionConfig()
    root = redact_path_string(str(config_dir), cfg)
    roots = ",".join(redact_path_string(str(p), cfg) for p in scan_roots) or "."
    if coverage_map:
        cov = "map=" + ",".join(
            f"{redact_path_string(prefix, cfg)}:{redact_path_string(str(path), cfg)}"
            for prefix, path in coverage_map.items()
        )
    elif coverage_path is not None:
        cov = f"single={redact_path_string(str(coverage_path), cfg)}"
    else:
        cov = "none"
    typer.secho(
        f"riskratchet: command={command} root={root} scan_roots=[{roots}] coverage={cov}",
        err=True,
    )


if __name__ == "__main__":
    app()
