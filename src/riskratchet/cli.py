"""Typer entrypoint for the riskratchet CLI.

Each command is a thin shell: load config, call `analyze` (and friends), pick
a renderer, write to stdout or `--output`. Business logic lives in the other
modules; this file should stay easy to scan.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from riskratchet import __version__
from riskratchet.auto_coverage import (
    DEFAULT_CACHE_PATH,
    DEFAULT_TEST_COMMAND,
    AutoCoverageResult,
    ensure_coverage,
)
from riskratchet.baseline import baseline_from_report, compare, load_baseline, save_baseline
from riskratchet.engine import analyze
from riskratchet.models import Regression, RiskReport
from riskratchet.reporting import (
    render_function_explanation,
    render_regressions_json,
    render_regressions_markdown,
    render_regressions_sarif,
    render_regressions_table,
    render_report_json,
    render_report_markdown,
    render_report_sarif,
    render_report_table,
)

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[import-not-found]

VALID_FORMATS = ("table", "json", "markdown", "sarif")
VALID_BASELINE_FORMATS = ("riskratchet",)

app = typer.Typer(
    help="A maintainability ratchet for AI-assisted Python.",
    no_args_is_help=True,
    add_completion=False,
)


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


@app.command()
def scan(
    paths: Annotated[list[Path], typer.Argument(help="Files or directories to scan.")],
    coverage: Annotated[Path | None, typer.Option("--coverage", help="Path to coverage.json.")] = None,
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
    include: Annotated[list[str] | None, typer.Option("--include", help="Glob include patterns.")] = None,
    exclude: Annotated[list[str] | None, typer.Option("--exclude", help="Glob exclude patterns.")] = None,
    no_git: Annotated[bool, typer.Option("--no-git", help="Disable churn collection.")] = False,
    limit: Annotated[int, typer.Option("--limit", help="Max table rows; 0 for all.")] = 20,
    no_auto_cov: Annotated[
        bool,
        typer.Option(
            "--no-auto-cov",
            help="Skip auto-generating coverage by running the test command.",
        ),
    ] = False,
) -> None:
    """Scan files and report risk; never fails."""
    cfg = _load_config(config)
    effective_format = _effective_format(format, json_output)
    resolved_paths = _resolved_paths(paths, cfg)
    coverage_path = _resolve_coverage(
        coverage,
        cfg,
        sources=resolved_paths,
        no_auto_cov=no_auto_cov,
        required=False,
        allow_missing=True,
    )
    report = analyze(
        resolved_paths,
        coverage_path=coverage_path,
        include=include or [],
        exclude=exclude or cfg.get("exclude", []),
        use_git=not no_git,
    )
    _emit_report(report, format=effective_format, output=output, limit=limit, quiet=quiet)


@app.command()
def baseline(
    paths: Annotated[list[Path], typer.Argument(help="Files or directories to baseline.")],
    coverage: Annotated[Path | None, typer.Option("--coverage")] = None,
    config: Annotated[Path | None, typer.Option("--config")] = None,
    output: Annotated[Path | None, typer.Option("--output", help="Where to write the baseline JSON.")] = None,
    include: Annotated[list[str] | None, typer.Option("--include")] = None,
    exclude: Annotated[list[str] | None, typer.Option("--exclude")] = None,
    no_git: Annotated[bool, typer.Option("--no-git")] = False,
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
    cfg = _load_config(config)
    resolved_paths = _resolved_paths(paths, cfg)
    coverage_path = _resolve_coverage(
        coverage,
        cfg,
        sources=resolved_paths,
        no_auto_cov=no_auto_cov,
        required=True,
        allow_missing=_resolved_bool(allow_missing_coverage, cfg.get("allow_missing_coverage")),
    )
    report = analyze(
        resolved_paths,
        coverage_path=coverage_path,
        include=include or [],
        exclude=exclude or cfg.get("exclude", []),
        use_git=not no_git,
    )
    target = output or Path(cfg.get("baseline", ".riskratchet.json"))
    save_baseline(baseline_from_report(report), target)
    typer.echo(f"wrote baseline with {len(report.functions)} functions to {target}")


@app.command()
def check(
    paths: Annotated[list[Path], typer.Argument(help="Files or directories to check.")],
    coverage: Annotated[Path | None, typer.Option("--coverage")] = None,
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
    no_git: Annotated[bool, typer.Option("--no-git")] = False,
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
) -> None:
    """Fail (exit 1) when risk regresses past tolerance."""
    cfg = _load_config(config)
    effective_format = _effective_format(format, json_output)
    _validate_baseline_format(baseline_format)
    baseline_file = baseline_path or Path(cfg.get("baseline", ".riskratchet.json"))
    if not baseline_file.exists():
        typer.secho(
            f"baseline file not found: {baseline_file}. Run `riskratchet baseline` first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    old = load_baseline(baseline_file)
    resolved_paths = _resolved_paths(paths, cfg)
    coverage_path = _resolve_coverage(
        coverage,
        cfg,
        sources=resolved_paths,
        no_auto_cov=no_auto_cov,
        required=True,
        allow_missing=_resolved_bool(allow_missing_coverage, cfg.get("allow_missing_coverage")),
    )
    report = analyze(
        resolved_paths,
        coverage_path=coverage_path,
        include=include or [],
        exclude=exclude or cfg.get("exclude", []),
        use_git=not no_git,
    )
    regressions = compare(
        report,
        old,
        fail_new_above=_resolved_float(fail_new_above, cfg.get("fail_new_above"), default=50.0),
        fail_regression_above=_resolved_float(
            fail_regression_above, cfg.get("fail_regression_above"), default=5.0
        ),
        fail_existing_above=_resolved_optional_float(fail_existing_above, cfg.get("fail_existing_above")),
        fail_component_regression_above=_resolved_float(
            fail_component_regression_above,
            cfg.get("fail_component_regression_above"),
            default=15.0,
        ),
        component_regression_gate=(
            not no_component_regression_gate
            and _resolved_bool(True, cfg.get("component_regression_gate"), default=True)
        ),
    )
    rendered = _render_regressions(regressions, format=effective_format)
    _write(rendered, output)
    if regressions:
        raise typer.Exit(code=1)


@app.command()
def explain(
    target: Annotated[str, typer.Argument(help="Function target as `path/to/file.py::qualname`.")],
    coverage: Annotated[Path | None, typer.Option("--coverage")] = None,
    config: Annotated[Path | None, typer.Option("--config")] = None,
    no_git: Annotated[bool, typer.Option("--no-git")] = False,
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
    cfg = _load_config(config)
    file_part, _ = target.split("::", 1)
    file_path = Path(file_part)
    coverage_path = _resolve_coverage(
        coverage,
        cfg,
        sources=[file_path],
        no_auto_cov=no_auto_cov,
        required=False,
        allow_missing=True,
    )
    report = analyze(
        [file_path],
        coverage_path=coverage_path,
        use_git=not no_git,
    )
    fn = report.find(target)
    if fn is None:
        typer.secho(f"function not found: {target}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    typer.echo(render_function_explanation(fn), nl=False)


def _emit_report(
    report: RiskReport, *, format: str, output: Path | None, limit: int, quiet: bool = False
) -> None:
    effective_limit = None if limit == 0 else limit
    if format == "json":
        rendered = render_report_json(report)
    elif format == "markdown":
        rendered = render_report_markdown(report, limit=effective_limit)
    elif format == "sarif":
        rendered = render_report_sarif(report)
    else:
        rendered = render_report_table(report, limit=effective_limit, include_summary=not quiet)
    _write(rendered, output)


def _effective_format(format: str, json_output: bool) -> str:
    if json_output:
        return "json"
    _validate_format(format)
    return format


def _render_regressions(regressions: list[Regression], *, format: str) -> str:
    if format == "json":
        return render_regressions_json(regressions)
    if format == "markdown":
        return render_regressions_markdown(regressions)
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


def _load_config(config_path: Path | None) -> dict[str, Any]:
    candidate = config_path or Path("pyproject.toml")
    if not candidate.exists():
        return {}
    try:
        raw = tomllib.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        typer.secho(f"warning: could not read {candidate}: {exc}", fg=typer.colors.YELLOW, err=True)
        return {}
    section = raw.get("tool", {}).get("riskratchet", {})
    return section if isinstance(section, dict) else {}


def _resolved_paths(paths: list[Path], cfg: dict[str, Any]) -> list[Path]:
    if paths:
        return paths
    configured = cfg.get("paths")
    if isinstance(configured, list) and configured:
        return [Path(p) for p in configured]
    return [Path(".")]


def _resolved_optional(value: Path | None, default: Any) -> Path | None:
    """Pick the explicit CLI value when given, else fall back to config.

    Config-derived paths must actually exist on disk; otherwise we treat them
    as absent. This keeps a stale `coverage = "coverage.json"` line in
    pyproject.toml from crashing the CLI in directories that never produced
    a coverage report.
    """
    if value is not None:
        return value
    if isinstance(default, str):
        candidate = Path(default)
    elif isinstance(default, Path):
        candidate = default
    else:
        return None
    return candidate if candidate.exists() else None


def _coverage_candidate(value: Path | None, default: Any) -> tuple[Path | None, bool]:
    if value is not None:
        return value, True
    if isinstance(default, str):
        return Path(default), True
    if isinstance(default, Path):
        return default, True
    return None, False


def _resolve_coverage(
    value: Path | None,
    cfg: dict[str, Any],
    *,
    sources: list[Path],
    no_auto_cov: bool,
    required: bool,
    allow_missing: bool,
) -> Path | None:
    """Resolve which coverage JSON to use, generating one via tests if needed.

    Precedence: an explicit existing `--coverage` path wins; then the
    configured `coverage` path if it exists; then the auto-coverage cache
    (regenerated by running the configured test command when stale). If
    everything fails and the command requires coverage, exit with code 2
    unless `--allow-missing-coverage` was set.
    """
    requested, was_configured = _coverage_candidate(value, cfg.get("coverage"))
    if requested is not None and requested.exists():
        return requested

    auto_enabled = not no_auto_cov and _resolved_bool(True, cfg.get("auto_coverage"), default=True)
    cache_path = Path(str(cfg.get("coverage_cache", str(DEFAULT_CACHE_PATH))))
    test_command = str(cfg.get("test_command", DEFAULT_TEST_COMMAND))

    result: AutoCoverageResult = ensure_coverage(
        requested=requested if was_configured else None,
        sources=sources,
        cache_path=cache_path,
        test_command=test_command,
        enabled=auto_enabled,
    )
    if result.path is not None:
        return result.path

    if not required or allow_missing:
        if requested is not None and value is not None and not requested.exists():
            typer.secho(
                f"warning: coverage file not found: {requested}; continuing without coverage.",
                fg=typer.colors.YELLOW,
                err=True,
            )
        return None

    typer.secho(
        (
            "coverage data is required but none could be produced. "
            f"Tried --coverage path ({requested}), the auto-coverage cache "
            f"({cache_path}), and `{test_command.format(output=str(cache_path))}`. "
            "Generate coverage manually, pass --allow-missing-coverage, "
            "or disable auto-generation with --no-auto-cov."
        ),
        fg=typer.colors.RED,
        err=True,
    )
    raise typer.Exit(code=2)


def _resolved_float(
    cli_value: float | None,
    cfg_value: Any,
    *,
    default: float,
) -> float:
    if cli_value is not None:
        return float(cli_value)
    if isinstance(cfg_value, (int, float)):
        return float(cfg_value)
    return default


def _resolved_optional_float(cli_value: float | None, cfg_value: Any) -> float | None:
    if cli_value is not None:
        return float(cli_value)
    if isinstance(cfg_value, (int, float)):
        return float(cfg_value)
    return None


def _resolved_bool(cli_value: bool, cfg_value: Any, *, default: bool = False) -> bool:
    if cli_value != default:
        return cli_value
    if isinstance(cfg_value, bool):
        return cfg_value
    return default


if __name__ == "__main__":
    app()
