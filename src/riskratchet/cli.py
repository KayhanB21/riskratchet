"""Typer entrypoint for the riskratchet CLI.

Each command is a thin shell: load config, call `analyze` (and friends), pick
a renderer, write to stdout or `--output`. Business logic lives in the other
modules; this file should stay easy to scan.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated

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
from riskratchet.engine import analyze
from riskratchet.models import DiffReport, Regression, RegressionKind, RiskReport
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
) -> None:
    """Scan files and report risk; never fails."""
    cfg, config_dir = _discover_config(config)
    _warn_unknown_config_keys(cfg)
    effective_format = _effective_format(format, json_output)
    resolved_paths = _resolved_paths(paths, cfg, config_dir)
    resolved_coverage_map = _resolved_coverage_map(coverage_map, cfg, config_dir)
    coverage_path: Path | None
    if resolved_coverage_map:
        _ensure_coverage_map_exists(resolved_coverage_map, allow_missing=True)
        coverage_path = None
    else:
        coverage_path = _resolve_coverage(
            coverage,
            cfg,
            sources=resolved_paths,
            no_auto_cov=no_auto_cov,
            required=False,
            allow_missing=True,
            config_dir=config_dir,
        )
    _emit_diagnostics_banner(
        command="scan",
        scan_roots=resolved_paths,
        coverage_path=coverage_path,
        config_dir=config_dir,
        coverage_map=resolved_coverage_map,
    )
    report = analyze(
        resolved_paths,
        root=config_dir,
        coverage_path=coverage_path,
        coverage_map=resolved_coverage_map or None,
        include=include or [],
        exclude=exclude or cfg.get("exclude", []),
        allow=allow or cfg.get("allow", []),
        use_git=not no_git,
        churn_days=_resolved_churn_days(churn_days, cfg),
        weights=_resolved_weights(cfg),
        missing_coverage_policy=_resolved_missing_coverage(missing_coverage, cfg),
        groups=_resolved_groups(cfg),
    )
    filtered = _filtered_report(report, min_score=min_score, top=top or (None if limit == 0 else limit))
    links = _resolve_source_links(repo_url, commit_ref)
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
) -> None:
    """Compute current risk and save it as the new baseline."""
    cfg, config_dir = _discover_config(config)
    _warn_unknown_config_keys(cfg)
    resolved_paths = _resolved_paths(paths, cfg, config_dir)
    allow_missing = _resolved_bool(allow_missing_coverage, cfg.get("allow_missing_coverage"))
    resolved_coverage_map = _resolved_coverage_map(coverage_map, cfg, config_dir)
    coverage_path: Path | None
    if resolved_coverage_map:
        _ensure_coverage_map_exists(resolved_coverage_map, allow_missing=allow_missing)
        coverage_path = None
    else:
        coverage_path = _resolve_coverage(
            coverage,
            cfg,
            sources=resolved_paths,
            no_auto_cov=no_auto_cov,
            required=True,
            allow_missing=allow_missing,
            config_dir=config_dir,
        )
    _emit_diagnostics_banner(
        command="baseline",
        scan_roots=resolved_paths,
        coverage_path=coverage_path,
        config_dir=config_dir,
        coverage_map=resolved_coverage_map,
    )
    report = analyze(
        resolved_paths,
        root=config_dir,
        coverage_path=coverage_path,
        coverage_map=resolved_coverage_map or None,
        include=include or [],
        exclude=exclude or cfg.get("exclude", []),
        allow=allow or cfg.get("allow", []),
        use_git=not no_git,
        churn_days=_resolved_churn_days(churn_days, cfg),
        weights=_resolved_weights(cfg),
        missing_coverage_policy=_resolved_missing_coverage(missing_coverage, cfg),
        groups=_resolved_groups(cfg),
    )
    target = output or _anchor_config_path(Path(cfg.get("baseline", ".riskratchet.json")), config_dir)
    save_baseline(baseline_from_report(report), target)
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
) -> None:
    """Fail (exit 1) when risk regresses past tolerance."""
    cfg, config_dir = _discover_config(config)
    _warn_unknown_config_keys(cfg)
    effective_format = _effective_format(format, json_output)
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
            f"baseline file not found: {baseline_file}. Run `riskratchet baseline` first, "
            f"or pass --fail-above N to gate on an absolute score threshold (no baseline).",
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
    if not baseline_present and effective_format == "pr-comment":
        typer.secho(
            "--format pr-comment requires a baseline; not supported in --fail-above-only mode.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    old = load_baseline(baseline_file) if baseline_present else None
    resolved_paths = _resolved_paths(paths, cfg, config_dir)
    allow_missing = _resolved_bool(allow_missing_coverage, cfg.get("allow_missing_coverage"))
    resolved_coverage_map = _resolved_coverage_map(coverage_map, cfg, config_dir)
    coverage_path: Path | None
    if resolved_coverage_map:
        _ensure_coverage_map_exists(resolved_coverage_map, allow_missing=allow_missing)
        coverage_path = None
    else:
        coverage_path = _resolve_coverage(
            coverage,
            cfg,
            sources=resolved_paths,
            no_auto_cov=no_auto_cov,
            required=True,
            allow_missing=allow_missing,
            config_dir=config_dir,
        )
    _emit_diagnostics_banner(
        command="check",
        scan_roots=resolved_paths,
        coverage_path=coverage_path,
        config_dir=config_dir,
        coverage_map=resolved_coverage_map,
    )
    report = analyze(
        resolved_paths,
        root=config_dir,
        coverage_path=coverage_path,
        coverage_map=resolved_coverage_map or None,
        include=include or [],
        exclude=exclude or cfg.get("exclude", []),
        allow=allow or cfg.get("allow", []),
        use_git=not no_git,
        churn_days=_resolved_churn_days(churn_days, cfg),
        weights=_resolved_weights(cfg),
        missing_coverage_policy=_resolved_missing_coverage(missing_coverage, cfg),
        groups=_resolved_groups(cfg),
    )
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
            fail_new_above=_resolved_float(fail_new_above, cfg.get("fail_new_above"), default=50.0),
            fail_existing_above=_resolved_optional_float(fail_existing_above, cfg.get("fail_existing_above")),
        )
    else:
        assert fail_above_resolved is not None
        diff_report = None
        regressions = regressions_above_threshold(report, threshold=fail_above_resolved)
    links = _resolve_source_links(repo_url, commit_ref)
    if summary:
        rendered = (
            render_regressions_summary_json(regressions, diff_report=diff_report)
            if effective_format == "json"
            else render_regressions_summary_text(regressions, diff_report=diff_report)
        )
    elif effective_format == "pr-comment":
        assert diff_report is not None
        rendered = render_diff_pr_comment(diff_report, links=links)
    else:
        rendered = _render_regressions(regressions, format=effective_format, links=links)
    _write(rendered, output)
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
) -> None:
    """Print full risk breakdown for one function."""
    if "::" not in target:
        raise typer.BadParameter("target must be `path::qualname` (e.g. src/foo.py::Bar.baz)")
    cfg, config_dir = _discover_config(config)
    _warn_unknown_config_keys(cfg)
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
    )
    report = analyze(
        [file_path],
        coverage_path=coverage_path,
        use_git=not no_git,
        churn_days=_resolved_churn_days(churn_days, cfg),
        weights=_resolved_weights(cfg),
        groups=_resolved_groups(cfg),
    )
    fn = report.find(target)
    if fn is None:
        typer.secho(f"function not found: {target}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    typer.echo(render_function_explanation(fn), nl=False)


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
) -> None:
    """Show full baseline diff; does not fail."""
    cfg, config_dir = _discover_config(config)
    _warn_unknown_config_keys(cfg)
    effective_format = _effective_format(format, json_output)
    baseline_file = baseline_path or _anchor_config_path(
        Path(cfg.get("baseline", ".riskratchet.json")), config_dir
    )
    if not baseline_file.exists():
        typer.secho(
            f"baseline file not found: {baseline_file}. Run `riskratchet baseline` first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    old = load_baseline(baseline_file)
    resolved_paths = _resolved_paths(paths, cfg, config_dir)
    allow_missing = _resolved_bool(allow_missing_coverage, cfg.get("allow_missing_coverage"))
    resolved_coverage_map = _resolved_coverage_map(coverage_map, cfg, config_dir)
    coverage_path: Path | None
    if resolved_coverage_map:
        _ensure_coverage_map_exists(resolved_coverage_map, allow_missing=allow_missing)
        coverage_path = None
    else:
        coverage_path = _resolve_coverage(
            coverage,
            cfg,
            sources=resolved_paths,
            no_auto_cov=no_auto_cov,
            required=True,
            allow_missing=allow_missing,
            config_dir=config_dir,
        )
    _emit_diagnostics_banner(
        command="diff",
        scan_roots=resolved_paths,
        coverage_path=coverage_path,
        config_dir=config_dir,
        coverage_map=resolved_coverage_map,
    )
    report = analyze(
        resolved_paths,
        root=config_dir,
        coverage_path=coverage_path,
        coverage_map=resolved_coverage_map or None,
        include=include or [],
        exclude=exclude or cfg.get("exclude", []),
        allow=allow or cfg.get("allow", []),
        use_git=not no_git,
        churn_days=_resolved_churn_days(churn_days, cfg),
        weights=_resolved_weights(cfg),
        missing_coverage_policy=_resolved_missing_coverage(missing_coverage, cfg),
        groups=_resolved_groups(cfg),
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
    links = _resolve_source_links(repo_url, commit_ref)
    if summary:
        rendered = (
            render_diff_summary_json(diff_report)
            if effective_format == "json"
            else render_diff_summary_text(diff_report)
        )
    elif effective_format == "json":
        rendered = render_diff_json(diff_report)
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
            )
        )
    else:
        rendered = render_diff_table(diff_report)
    _write(rendered, output)


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
        rendered = render_report_json(report)
    elif format == "markdown":
        rendered = render_report_markdown(report, limit=effective_limit, links=links)
    elif format == "sarif":
        rendered = render_report_sarif(report, min_score=min_score if min_score is not None else 25.0)
    elif format == "github":
        rendered = render_report_github(report, min_score=min_score if min_score is not None else 25.0)
    elif format == "pr-comment":
        rendered = render_report_pr_comment(report, limit=effective_limit, links=links)
    else:
        rendered = render_report_table(report, limit=effective_limit, include_summary=not quiet)
    _write(rendered, output)


def _effective_format(format: str, json_output: bool) -> str:
    if json_output:
        return "json"
    _validate_format(format)
    return format


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
        return render_regressions_json(regressions)
    if format == "markdown":
        return render_regressions_markdown(regressions, links=links)
    if format == "pr-comment":
        return render_regressions_pr_comment(regressions, links=links)
    if format == "github":
        return render_regressions_github(regressions)
    if format == "sarif":
        return render_regressions_sarif(regressions)
    return render_regressions_table(regressions)


def _write(rendered: str, output: Path | None) -> None:
    if output is None:
        typer.echo(rendered, nl=False)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")


def _validate_format(format: str) -> None:
    if format not in VALID_FORMATS:
        raise typer.BadParameter(f"format must be one of {', '.join(VALID_FORMATS)}")


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


def _emit_diagnostics_banner(
    *,
    command: str,
    scan_roots: list[Path],
    coverage_path: Path | None,
    config_dir: Path,
    coverage_map: Mapping[str, Path] | None = None,
) -> None:
    """Print a single 'resolved root + coverage source' line to stderr.

    Always-on so monorepo users can see which package is being scanned with
    which coverage file. `root` is the discovered config directory (which
    equals the current directory unless config was found in an ancestor).
    Stdout stays payload-only.
    """
    roots = ",".join(str(p) for p in scan_roots) or "."
    if coverage_map:
        cov = "map=" + ",".join(f"{prefix}:{path}" for prefix, path in coverage_map.items())
    elif coverage_path is not None:
        cov = f"single={coverage_path}"
    else:
        cov = "none"
    typer.secho(
        f"riskratchet: command={command} root={config_dir} scan_roots=[{roots}] coverage={cov}",
        err=True,
    )


if __name__ == "__main__":
    app()
