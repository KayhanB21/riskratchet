"""Typer entrypoint for the riskratchet CLI.

Each command is a thin shell: load config, call `analyze` (and friends), pick
a renderer, write to stdout or `--output`. Business logic lives in the other
modules; this file should stay easy to scan.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any

import typer

from riskratchet import __version__
from riskratchet.baseline import (
    baseline_from_report,
    load_baseline,
    regressions_above_threshold,
    regressions_from_diff,
    save_baseline,
)
from riskratchet.baseline import (
    diff as diff_baseline,
)
from riskratchet.config import (
    CONFIG_SCHEMA_URL,
    _anchor_config_path,
    _discover_config,
    _ensure_coverage_map_exists,
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
    _warn_unknown_config_keys,
)
from riskratchet.diagnostics import Diagnostics, write_debug_json
from riskratchet.doctor import CheckStatus, DoctorCheck, diagnose, summarize
from riskratchet.engine import analyze
from riskratchet.git import head_sha
from riskratchet.init import (
    InitOutcome,
    RunnerKind,
    detect_test_runner,
    render_ci_snippet,
    write_starter_config,
)
from riskratchet.models import Baseline, DiffReport, Regression, RegressionKind, RiskReport, Severity
from riskratchet.redaction import (
    RedactionConfig,
    redact_diagnostics,
    redact_diff,
    redact_function,
    redact_path_string,
    redact_regressions,
    redact_report,
    resolve_salt,
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

app = typer.Typer(
    help="A maintainability ratchet for AI-assisted Python.",
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
    """Validate `[tool.riskratchet]` configuration."""
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
        typer.Argument(
            help="Files or directories to scan. Falls back to [tool.riskratchet] paths if omitted."
        ),
    ] = None,
    coverage: Annotated[Path | None, typer.Option("--coverage", help="Path to coverage.json.")] = None,
    coverage_map: Annotated[
        list[str] | None,
        typer.Option(
            "--coverage-map",
            help="Per-prefix coverage path, repeatable: --coverage-map packages/a=cov-a.json.",
        ),
    ] = None,
    config: Annotated[Path | None, typer.Option("--config", help="Path to pyproject.toml.")] = None,
    format: Annotated[str, typer.Option("--format", help="Output format.")] = "table",
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
    output: Annotated[Path | None, typer.Option("--output", help="Write output to file.")] = None,
    summary: Annotated[bool, typer.Option("--summary", help="Emit aggregate summary only.")] = False,
    include: Annotated[list[str] | None, typer.Option("--include", help="Glob include patterns.")] = None,
    exclude: Annotated[list[str] | None, typer.Option("--exclude", help="Glob exclude patterns.")] = None,
    allow: Annotated[
        list[str] | None,
        typer.Option("--allow", help="Suppress matching functions or path globs from reporting/gating."),
    ] = None,
    no_git: Annotated[bool, typer.Option("--no-git", help="Disable churn collection.")] = False,
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
        typer.Option("--fail-severity", help="Exit 1 if any emitted function is at least this severity."),
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
    experimental_typescript: Annotated[
        bool,
        typer.Option(
            "--experimental-typescript",
            help="EXPERIMENTAL: also list discovered TypeScript functions (informational only; "
            "no scoring or gating; needs `pip install 'riskratchet[typescript]'`). Output may change.",
        ),
    ] = False,
    ts_coverage: Annotated[
        Path | None,
        typer.Option(
            "--ts-coverage",
            help="EXPERIMENTAL: Istanbul/nyc coverage-final.json to annotate discovered "
            "TypeScript functions with line/branch coverage. Only used with "
            "--experimental-typescript; separate from --coverage (which is Python).",
        ),
    ] = None,
) -> None:
    """Scan files and report risk; never fails."""
    cfg, config_dir = _discover_config(config)
    _warn_unknown_config_keys(cfg)
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
    resolved_coverage_map = _resolved_coverage_map(coverage_map, cfg, config_dir)
    coverage_path: Path | None
    if resolved_coverage_map:
        _ensure_coverage_map_exists(resolved_coverage_map, allow_missing=True)
        coverage_path = None
        diag.set_coverage(
            mode="map",
            source="map",
            coverage_map={prefix: str(path) for prefix, path in resolved_coverage_map.items()},
        )
    else:
        coverage_path = _resolve_coverage(
            coverage,
            cfg,
            sources=resolved_paths,
            no_auto_cov=no_auto_cov,
            required=False,
            allow_missing=True,
            config_dir=config_dir,
            diagnostics=diag,
        )
    _emit_diagnostics_banner(
        command="scan",
        scan_roots=resolved_paths,
        coverage_path=coverage_path,
        config_dir=config_dir,
        coverage_map=resolved_coverage_map,
        redaction=redaction,
    )
    resolved_include = include or []
    resolved_exclude = exclude or cfg.get("exclude", [])
    resolved_allow = allow or cfg.get("allow", [])
    resolved_churn_days = _resolved_churn_days(churn_days, cfg)
    report = analyze(
        resolved_paths,
        root=config_dir,
        coverage_path=coverage_path,
        coverage_map=resolved_coverage_map or None,
        include=resolved_include,
        exclude=resolved_exclude,
        allow=resolved_allow,
        use_git=not no_git,
        churn_days=resolved_churn_days,
        weights=_resolved_weights(cfg),
        missing_coverage_policy=_resolved_missing_coverage(missing_coverage, cfg),
        groups=_resolved_groups(cfg),
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
    if experimental_typescript:
        _emit_typescript_discovery(
            resolved_paths,
            root=config_dir,
            include=resolved_include,
            exclude=resolved_exclude,
            ts_coverage=ts_coverage,
        )
    elif ts_coverage is not None:
        typer.secho(
            "typescript: --ts-coverage has no effect without --experimental-typescript.",
            fg=typer.colors.YELLOW,
            err=True,
        )
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
        typer.Argument(
            help="Files or directories to baseline. Falls back to [tool.riskratchet] paths if omitted."
        ),
    ] = None,
    coverage: Annotated[Path | None, typer.Option("--coverage")] = None,
    coverage_map: Annotated[
        list[str] | None,
        typer.Option("--coverage-map", help="Per-prefix coverage path, repeatable."),
    ] = None,
    config: Annotated[Path | None, typer.Option("--config")] = None,
    output: Annotated[Path | None, typer.Option("--output", help="Where to write the baseline JSON.")] = None,
    include: Annotated[list[str] | None, typer.Option("--include")] = None,
    exclude: Annotated[list[str] | None, typer.Option("--exclude")] = None,
    allow: Annotated[list[str] | None, typer.Option("--allow")] = None,
    no_git: Annotated[bool, typer.Option("--no-git")] = False,
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
) -> None:
    """Compute current risk and save it as the new baseline.

    Redaction flags are intentionally not accepted here: the baseline file is
    the source of truth for future rename matching and must never be hashed.
    """
    cfg, config_dir = _discover_config(config)
    _warn_unknown_config_keys(cfg)
    diag = Diagnostics(command="baseline")
    resolved_paths = _resolved_paths(paths, cfg, config_dir)
    _check_paths_exist(resolved_paths, paths_arg=paths, configured=cfg.get("paths"))
    allow_missing = _resolved_bool(allow_missing_coverage, cfg.get("allow_missing_coverage"))
    resolved_coverage_map = _resolved_coverage_map(coverage_map, cfg, config_dir)
    coverage_path: Path | None
    if resolved_coverage_map:
        _ensure_coverage_map_exists(resolved_coverage_map, allow_missing=allow_missing)
        coverage_path = None
        diag.set_coverage(
            mode="map",
            source="map",
            coverage_map={prefix: str(path) for prefix, path in resolved_coverage_map.items()},
        )
    else:
        coverage_path = _resolve_coverage(
            coverage,
            cfg,
            sources=resolved_paths,
            no_auto_cov=no_auto_cov,
            required=True,
            allow_missing=allow_missing,
            config_dir=config_dir,
            diagnostics=diag,
        )
    _emit_diagnostics_banner(
        command="baseline",
        scan_roots=resolved_paths,
        coverage_path=coverage_path,
        config_dir=config_dir,
        coverage_map=resolved_coverage_map,
        redaction=RedactionConfig(),
    )
    resolved_include = include or []
    resolved_exclude = exclude or cfg.get("exclude", [])
    resolved_allow = allow or cfg.get("allow", [])
    resolved_churn_days = _resolved_churn_days(churn_days, cfg)
    report = analyze(
        resolved_paths,
        root=config_dir,
        coverage_path=coverage_path,
        coverage_map=resolved_coverage_map or None,
        include=resolved_include,
        exclude=resolved_exclude,
        allow=resolved_allow,
        use_git=not no_git,
        churn_days=resolved_churn_days,
        weights=_resolved_weights(cfg),
        missing_coverage_policy=_resolved_missing_coverage(missing_coverage, cfg),
        groups=_resolved_groups(cfg),
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
    save_baseline(baseline_from_report(report), target)
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
        typer.Argument(
            help="Files or directories to check. Falls back to [tool.riskratchet] paths if omitted."
        ),
    ] = None,
    coverage: Annotated[Path | None, typer.Option("--coverage")] = None,
    coverage_map: Annotated[
        list[str] | None,
        typer.Option("--coverage-map", help="Per-prefix coverage path, repeatable."),
    ] = None,
    baseline_path: Annotated[Path | None, typer.Option("--baseline", help="Path to baseline JSON.")] = None,
    config: Annotated[Path | None, typer.Option("--config")] = None,
    format: Annotated[str, typer.Option("--format")] = "table",
    json_output: Annotated[
        bool, typer.Option("--json", help="Shortcut for --format json. Overrides --format.")
    ] = False,
    baseline_format: Annotated[
        str,
        typer.Option(
            "--baseline-format",
            help="Baseline input format. Currently only 'riskratchet' is supported.",
        ),
    ] = "riskratchet",
    output: Annotated[Path | None, typer.Option("--output")] = None,
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
    fail_new_above: Annotated[float | None, typer.Option("--fail-new-above")] = None,
    fail_regression_above: Annotated[float | None, typer.Option("--fail-regression-above")] = None,
    fail_existing_above: Annotated[float | None, typer.Option("--fail-existing-above")] = None,
    fail_component_regression_above: Annotated[
        float | None,
        typer.Option("--fail-component-regression-above"),
    ] = None,
    no_component_regression_gate: Annotated[
        bool,
        typer.Option(
            "--no-component-regression-gate",
            help="Disable per-component regression checks.",
        ),
    ] = False,
    include: Annotated[list[str] | None, typer.Option("--include")] = None,
    exclude: Annotated[list[str] | None, typer.Option("--exclude")] = None,
    allow: Annotated[list[str] | None, typer.Option("--allow")] = None,
    no_git: Annotated[bool, typer.Option("--no-git")] = False,
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
) -> None:
    """Fail (exit 1) when risk regresses past tolerance."""
    cfg, config_dir = _discover_config(config)
    _warn_unknown_config_keys(cfg)
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
    allow_missing = _resolved_bool(allow_missing_coverage, cfg.get("allow_missing_coverage"))
    resolved_coverage_map = _resolved_coverage_map(coverage_map, cfg, config_dir)
    coverage_path: Path | None
    if resolved_coverage_map:
        _ensure_coverage_map_exists(resolved_coverage_map, allow_missing=allow_missing)
        coverage_path = None
        diag.set_coverage(
            mode="map",
            source="map",
            coverage_map={prefix: str(path) for prefix, path in resolved_coverage_map.items()},
        )
    else:
        coverage_path = _resolve_coverage(
            coverage,
            cfg,
            sources=resolved_paths,
            no_auto_cov=no_auto_cov,
            required=True,
            allow_missing=allow_missing,
            config_dir=config_dir,
            diagnostics=diag,
        )
    _emit_diagnostics_banner(
        command="check",
        scan_roots=resolved_paths,
        coverage_path=coverage_path,
        config_dir=config_dir,
        coverage_map=resolved_coverage_map,
        redaction=redaction,
    )
    resolved_include = include or []
    resolved_exclude = exclude or cfg.get("exclude", [])
    resolved_allow = allow or cfg.get("allow", [])
    resolved_churn_days = _resolved_churn_days(churn_days, cfg)
    report = analyze(
        resolved_paths,
        root=config_dir,
        coverage_path=coverage_path,
        coverage_map=resolved_coverage_map or None,
        include=resolved_include,
        exclude=resolved_exclude,
        allow=resolved_allow,
        use_git=not no_git,
        churn_days=resolved_churn_days,
        weights=_resolved_weights(cfg),
        missing_coverage_policy=_resolved_missing_coverage(missing_coverage, cfg),
        groups=_resolved_groups(cfg),
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
        # baseline and `--fail-above` modes.
        if diff_report is not None:
            rendered = render_diff_pr_comment(diff_report, links=links)
        else:
            rendered = render_regressions_pr_comment(regressions, links=links)
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
    coverage: Annotated[Path | None, typer.Option("--coverage")] = None,
    config: Annotated[Path | None, typer.Option("--config")] = None,
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
    no_git: Annotated[bool, typer.Option("--no-git")] = False,
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
) -> None:
    """Print full risk breakdown for one function."""
    if "::" not in target:
        raise typer.BadParameter("target must be `path::qualname` (e.g. src/foo.py::Bar.baz)")
    cfg, config_dir = _discover_config(config)
    _warn_unknown_config_keys(cfg)
    diag = Diagnostics(command="explain")
    file_part, _ = target.split("::", 1)
    file_path = Path(file_part)
    coverage_path = _resolve_coverage(
        coverage,
        cfg,
        sources=[file_path],
        no_auto_cov=no_auto_cov,
        required=False,
        allow_missing=True,
        config_dir=config_dir,
        diagnostics=diag,
    )
    resolved_churn_days = _resolved_churn_days(churn_days, cfg)
    report = analyze(
        [file_path],
        coverage_path=coverage_path,
        use_git=not no_git,
        churn_days=resolved_churn_days,
        weights=_resolved_weights(cfg),
        groups=_resolved_groups(cfg),
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
        typer.secho(f"function not found: {target}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    redaction = _resolve_redaction(
        redact_paths=redact_paths,
        redact_qualnames=redact_qualnames,
        private_comment=private_comment,
        redact_salt=redact_salt,
        cfg=cfg,
        config_dir=config_dir,
    )
    fn = redact_function(fn, redaction)
    links = _links_for(repo_url, commit_ref, redaction)
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
        typer.Argument(
            help=(
                "Files or directories to diff against baseline. "
                "Falls back to [tool.riskratchet] paths if omitted."
            )
        ),
    ] = None,
    coverage: Annotated[Path | None, typer.Option("--coverage")] = None,
    coverage_map: Annotated[
        list[str] | None,
        typer.Option("--coverage-map", help="Per-prefix coverage path, repeatable."),
    ] = None,
    baseline_path: Annotated[Path | None, typer.Option("--baseline", help="Path to baseline JSON.")] = None,
    config: Annotated[Path | None, typer.Option("--config")] = None,
    format: Annotated[str, typer.Option("--format")] = "table",
    json_output: Annotated[
        bool, typer.Option("--json", help="Shortcut for --format json. Overrides --format.")
    ] = False,
    output: Annotated[Path | None, typer.Option("--output")] = None,
    summary: Annotated[bool, typer.Option("--summary", help="Emit aggregate summary only.")] = False,
    fail_regression_above: Annotated[float | None, typer.Option("--fail-regression-above")] = None,
    fail_component_regression_above: Annotated[
        float | None,
        typer.Option("--fail-component-regression-above"),
    ] = None,
    no_component_regression_gate: Annotated[
        bool,
        typer.Option("--no-component-regression-gate"),
    ] = False,
    include: Annotated[list[str] | None, typer.Option("--include")] = None,
    exclude: Annotated[list[str] | None, typer.Option("--exclude")] = None,
    allow: Annotated[list[str] | None, typer.Option("--allow")] = None,
    no_git: Annotated[bool, typer.Option("--no-git")] = False,
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
) -> None:
    """Show full baseline diff; does not fail."""
    cfg, config_dir = _discover_config(config)
    _warn_unknown_config_keys(cfg)
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
    allow_missing = _resolved_bool(allow_missing_coverage, cfg.get("allow_missing_coverage"))
    resolved_coverage_map = _resolved_coverage_map(coverage_map, cfg, config_dir)
    coverage_path: Path | None
    if resolved_coverage_map:
        _ensure_coverage_map_exists(resolved_coverage_map, allow_missing=allow_missing)
        coverage_path = None
        diag.set_coverage(
            mode="map",
            source="map",
            coverage_map={prefix: str(path) for prefix, path in resolved_coverage_map.items()},
        )
    else:
        coverage_path = _resolve_coverage(
            coverage,
            cfg,
            sources=resolved_paths,
            no_auto_cov=no_auto_cov,
            required=True,
            allow_missing=allow_missing,
            config_dir=config_dir,
            diagnostics=diag,
        )
    _emit_diagnostics_banner(
        command="diff",
        scan_roots=resolved_paths,
        coverage_path=coverage_path,
        config_dir=config_dir,
        coverage_map=resolved_coverage_map,
        redaction=redaction,
    )
    resolved_include = include or []
    resolved_exclude = exclude or cfg.get("exclude", [])
    resolved_allow = allow or cfg.get("allow", [])
    resolved_churn_days = _resolved_churn_days(churn_days, cfg)
    report = analyze(
        resolved_paths,
        root=config_dir,
        coverage_path=coverage_path,
        coverage_map=resolved_coverage_map or None,
        include=resolved_include,
        exclude=resolved_exclude,
        allow=resolved_allow,
        use_git=not no_git,
        churn_days=resolved_churn_days,
        weights=_resolved_weights(cfg),
        missing_coverage_policy=_resolved_missing_coverage(missing_coverage, cfg),
        groups=_resolved_groups(cfg),
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
            help="Replace an existing [tool.riskratchet] block in place.",
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
    """Scaffold `[tool.riskratchet]` config + print the CI snippet.

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
    src_dir = config_dir / "src"
    scan_paths = [src_dir] if src_dir.exists() else [config_dir]
    report = analyze(scan_paths, root=config_dir, coverage_path=coverage_path)
    baseline_file = config_dir / ".riskratchet.json"
    save_baseline(baseline_from_report(report), baseline_file)
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

    Exits 0 only when every check is `pass` or `warn`. A single `fail` exits 1
    with the remediation command in the per-check row, so a user can
    copy-paste the fix instead of guessing.
    """
    cfg, config_dir = _discover_config(config)
    _warn_unknown_config_keys(cfg)
    paths = _resolved_paths(None, cfg, config_dir)
    baseline_file = _anchor_config_path(Path(cfg.get("baseline", ".riskratchet.json")), config_dir)
    coverage_value = cfg.get("coverage")
    coverage_path: Path | None = None
    if isinstance(coverage_value, str):
        coverage_path = _anchor_config_path(Path(coverage_value), config_dir)
    checks = diagnose(
        config_dir=config_dir,
        cfg=cfg,
        paths=paths,
        baseline_file=baseline_file,
        coverage_path=coverage_path,
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
        typer.echo(f"  {status_glyph[check.status]}  {check.name:<13} {check.summary}")
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
            report, min_score=min_score if min_score is not None else 25.0, links=links
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
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")


def _emit_typescript_discovery(
    paths: list[Path],
    *,
    root: Path,
    include: list[str],
    exclude: list[str],
    ts_coverage: Path | None = None,
) -> None:
    """EXPERIMENTAL (P20, slice 2 since 0.2.12; coverage slice 3 since 0.2.13): list
    discovered TypeScript functions, optionally annotated with Istanbul coverage.

    Informational only — no scoring, no baseline, no gating; does not affect the exit code.
    The whole listing (banner, skip warnings, and the function list) goes to STDERR: it is an
    experimental diagnostic, not part of the machine-readable contract, so `--json` /
    `--format sarif` / `--output` keep emitting valid output on stdout. JSON/SARIF integration
    is deferred to a later slice. tree-sitter is imported lazily, so a default Python-only
    install never touches it.
    """
    from . import typescript as ts
    from . import typescript_coverage as tscov
    from ._paths import relative_posix

    typer.secho(
        "experimental: TypeScript discovery is informational and its output may change.",
        fg=typer.colors.YELLOW,
        err=True,
    )
    files = ts.iter_typescript_files(paths, root=root, include=include, exclude=exclude)
    if not files and exclude:
        typer.secho(
            "typescript: no .ts/.tsx/.mts/.cts files matched "
            f"(exclude patterns active: {', '.join(exclude)})",
            fg=typer.colors.YELLOW,
            err=True,
        )

    coverage = tscov.empty_istanbul_coverage()
    if ts_coverage is not None:
        try:
            coverage = tscov.load_istanbul_coverage(ts_coverage)
        except FileNotFoundError:
            typer.secho(
                f"typescript: --ts-coverage file not found: {ts_coverage} (listing without coverage).",
                fg=typer.colors.YELLOW,
                err=True,
            )
        except ValueError as exc:
            typer.secho(
                f"typescript: could not read --ts-coverage: {exc} (listing without coverage).",
                fg=typer.colors.YELLOW,
                err=True,
            )

    def _warn_skip(path: Path, message: str) -> None:
        typer.secho(f"skipping {relative_posix(path, root)}: {message}", fg=typer.colors.YELLOW, err=True)

    functions: list[ts.TsFunction] = []
    try:
        for path in files:
            found = ts.discover_typescript(path, root=root, on_error=_warn_skip)
            functions.extend(_attach_ts_coverage(found, coverage, ts_coverage is not None))
    except ImportError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    functions.sort(key=lambda fn: (fn.id.path, fn.span.start_line, fn.id.qualname))
    lines = [f"typescript: {len(functions)} function(s) in {len(files)} file(s)"]
    for fn in functions:
        visibility = "public" if fn.is_public else "internal"
        line = f"  {fn.id.as_target()}  [{visibility}]  ({fn.span.start_line}-{fn.span.end_line})"
        line += _format_ts_coverage(fn.coverage)
        lines.append(line)
    typer.echo("\n".join(lines), err=True)


def _attach_ts_coverage(
    functions: list[Any],
    coverage: Any,
    has_coverage: bool,
) -> list[Any]:
    """Enrich each discovered TsFunction with `CoverageStats` from the Istanbul report.

    Uses the SKIP missing-file policy: this is an informational listing, so a file the
    coverage run never measured renders with no coverage tag rather than a misleading 0%.
    Returns the functions unchanged when no --ts-coverage was given.
    """
    if not has_coverage:
        return functions
    from dataclasses import replace

    from .coverage import MissingCoveragePolicy
    from .typescript_coverage import coverage_for_ts_span

    out = []
    for fn in functions:
        file_cov = coverage.lookup(fn.id.path)
        stats = coverage_for_ts_span(file_cov, fn.span, missing_policy=MissingCoveragePolicy.SKIP)
        out.append(replace(fn, coverage=stats if file_cov is not None else None))
    return out


def _format_ts_coverage(coverage: Any) -> str:
    """Render the coverage annotation appended to a function's listing line, or '' when the
    function's file had no coverage entry."""
    if coverage is None:
        return ""
    parts = [f"cov {round(coverage.line_coverage * 100)}% line"]
    if coverage.branch_coverage is not None:
        parts.append(f"{round(coverage.branch_coverage * 100)}% branch")
    annotation = "  " + " / ".join(parts)
    if coverage.missing_lines:
        annotation += "  miss-lines " + ",".join(str(line) for line in coverage.missing_lines)
    return annotation


def _validate_format(format: str) -> None:
    if format not in VALID_FORMATS:
        raise typer.BadParameter(f"format must be one of {', '.join(VALID_FORMATS)}")


def _load_baseline_or_exit(baseline_file: Path) -> Baseline:
    """Load a baseline, converting parse failures into actionable stderr.

    `load_baseline` raises `ValueError` on a malformed file (junk JSON,
    truncated write, etc.); rather than dump that traceback on the user,
    re-emit it as a remediation-form setup error pointing at the next
    command to run.
    """
    try:
        return load_baseline(baseline_file)
    except ValueError as exc:
        typer.secho(
            _format_setup_error(
                f"riskratchet: cannot read baseline {baseline_file}: {exc}",
                [("Regenerate the baseline from current risk:", "riskratchet baseline")],
            ),
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2) from exc


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
        payload = write_debug_json(diag, debug_json_file)
        if payload is not None:
            typer.echo(payload, err=True)


def _resolve_redaction(
    *,
    redact_paths: bool,
    redact_qualnames: bool,
    private_comment: bool,
    redact_salt: str | None,
    cfg: Mapping[str, Any],
    config_dir: Path,
) -> RedactionConfig:
    """Build a RedactionConfig from CLI flags, config, and the salt sources.

    `--private-comment` is a preset: it forces both path and qualname redaction
    and suppresses source links for PR comments. When redaction is active but no
    explicit / env / config / git-derived salt exists, warn once that unsalted
    hashes are guessable.
    """
    rp = _resolved_bool(redact_paths, cfg.get("redact_paths"))
    rq = _resolved_bool(redact_qualnames, cfg.get("redact_qualnames"))
    pc = _resolved_bool(private_comment, cfg.get("private_comment"))
    if pc:
        rp = True
        rq = True
    if not (rp or rq):
        # Inactive: skip salt resolution entirely so a normal run never shells
        # out to git for a salt it will not use.
        return RedactionConfig()

    def _auto_salt() -> str | None:
        repo = _env("GITHUB_REPOSITORY")
        sha = _env("GITHUB_SHA")
        if repo and sha:
            return f"{repo}@{sha}"
        return head_sha(config_dir)

    resolution = resolve_salt(redact_salt, cfg.get("redact_salt"), auto=_auto_salt)
    if (rp or rq) and resolution.source == "none":
        typer.secho(
            "warning: redacting without a salt; hashes are guessable from known paths. "
            "Set --redact-salt or RISKRATCHET_REDACT_SALT for stronger redaction.",
            fg=typer.colors.YELLOW,
            err=True,
        )
    return RedactionConfig(
        redact_paths=rp,
        redact_qualnames=rq,
        suppress_links=pc,
        salt=resolution.salt,
    )


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
