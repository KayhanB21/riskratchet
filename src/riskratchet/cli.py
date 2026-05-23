"""Typer entrypoint for the riskratchet CLI.

Each command is a thin shell: load config, call `analyze` (and friends), pick
a renderer, write to stdout or `--output`. Business logic lives in the other
modules; this file should stay easy to scan.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Any, Optional

import typer

from riskratchet import __version__
from riskratchet.baseline import baseline_from_report, compare, load_baseline, save_baseline
from riskratchet.engine import analyze
from riskratchet.reporting import (
    REGRESSION_RENDERERS,
    REPORT_RENDERERS,
    render_function_explanation,
)

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

VALID_FORMATS = ("table", "json", "markdown")

app = typer.Typer(
    help="A maintainability ratchet for AI-assisted Python.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def _root(
    version: Annotated[bool, typer.Option("--version", help="Show version and exit.")] = False,
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()


@app.command()
def scan(
    paths: Annotated[list[Path], typer.Argument(help="Files or directories to scan.")],
    coverage: Annotated[Optional[Path], typer.Option("--coverage", help="Path to coverage.json.")] = None,
    config: Annotated[Optional[Path], typer.Option("--config", help="Path to pyproject.toml.")] = None,
    format: Annotated[str, typer.Option("--format", help="Output format.")] = "table",
    output: Annotated[Optional[Path], typer.Option("--output", help="Write output to file instead of stdout.")] = None,
    include: Annotated[Optional[list[str]], typer.Option("--include", help="Glob include patterns.")] = None,
    exclude: Annotated[Optional[list[str]], typer.Option("--exclude", help="Glob exclude patterns.")] = None,
    no_git: Annotated[bool, typer.Option("--no-git", help="Disable git churn collection.")] = False,
    limit: Annotated[int, typer.Option("--limit", help="Max rows in table/markdown output. Use 0 for all.")] = 20,
) -> None:
    """Scan files and report risk; never fails."""
    cfg = _load_config(config)
    _validate_format(format)
    report = analyze(
        _resolved_paths(paths, cfg),
        coverage_path=_resolved_optional(coverage, cfg.get("coverage")),
        include=include or [],
        exclude=exclude or cfg.get("exclude", []),
        use_git=not no_git,
    )
    _emit_report(report, format=format, output=output, limit=limit)


@app.command()
def baseline(
    paths: Annotated[list[Path], typer.Argument(help="Files or directories to baseline.")],
    coverage: Annotated[Optional[Path], typer.Option("--coverage")] = None,
    config: Annotated[Optional[Path], typer.Option("--config")] = None,
    output: Annotated[Optional[Path], typer.Option("--output", help="Where to write the baseline JSON.")] = None,
    include: Annotated[Optional[list[str]], typer.Option("--include")] = None,
    exclude: Annotated[Optional[list[str]], typer.Option("--exclude")] = None,
    no_git: Annotated[bool, typer.Option("--no-git")] = False,
) -> None:
    """Compute current risk and save it as the new baseline."""
    cfg = _load_config(config)
    report = analyze(
        _resolved_paths(paths, cfg),
        coverage_path=_resolved_optional(coverage, cfg.get("coverage")),
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
    coverage: Annotated[Optional[Path], typer.Option("--coverage")] = None,
    baseline_path: Annotated[Optional[Path], typer.Option("--baseline", help="Path to the baseline JSON.")] = None,
    config: Annotated[Optional[Path], typer.Option("--config")] = None,
    format: Annotated[str, typer.Option("--format")] = "table",
    output: Annotated[Optional[Path], typer.Option("--output")] = None,
    fail_new_above: Annotated[Optional[float], typer.Option("--fail-new-above")] = None,
    fail_regression_above: Annotated[Optional[float], typer.Option("--fail-regression-above")] = None,
    include: Annotated[Optional[list[str]], typer.Option("--include")] = None,
    exclude: Annotated[Optional[list[str]], typer.Option("--exclude")] = None,
    no_git: Annotated[bool, typer.Option("--no-git")] = False,
) -> None:
    """Fail (exit 1) when risk regresses past tolerance."""
    cfg = _load_config(config)
    _validate_format(format)
    baseline_file = baseline_path or Path(cfg.get("baseline", ".riskratchet.json"))
    if not baseline_file.exists():
        typer.secho(
            f"baseline file not found: {baseline_file}. Run `riskratchet baseline` first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    old = load_baseline(baseline_file)
    report = analyze(
        _resolved_paths(paths, cfg),
        coverage_path=_resolved_optional(coverage, cfg.get("coverage")),
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
    )
    rendered = REGRESSION_RENDERERS[format](regressions)
    _write(rendered, output)
    if regressions:
        raise typer.Exit(code=1)


@app.command()
def explain(
    target: Annotated[str, typer.Argument(help="Function target as `path/to/file.py::qualname`.")],
    coverage: Annotated[Optional[Path], typer.Option("--coverage")] = None,
    config: Annotated[Optional[Path], typer.Option("--config")] = None,
    no_git: Annotated[bool, typer.Option("--no-git")] = False,
) -> None:
    """Print full risk breakdown for one function."""
    if "::" not in target:
        raise typer.BadParameter("target must be `path::qualname` (e.g. src/foo.py::Bar.baz)")
    cfg = _load_config(config)
    file_part, _ = target.split("::", 1)
    report = analyze(
        [Path(file_part)],
        coverage_path=_resolved_optional(coverage, cfg.get("coverage")),
        use_git=not no_git,
    )
    fn = report.find(target)
    if fn is None:
        typer.secho(f"function not found: {target}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    typer.echo(render_function_explanation(fn), nl=False)


def _emit_report(report: Any, *, format: str, output: Optional[Path], limit: int) -> None:
    renderer = REPORT_RENDERERS[format]
    rendered = renderer(report, limit=None if limit == 0 else limit) if format != "json" else renderer(report)
    _write(rendered, output)


def _write(rendered: str, output: Optional[Path]) -> None:
    if output is None:
        typer.echo(rendered, nl=False)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")


def _validate_format(format: str) -> None:
    if format not in VALID_FORMATS:
        raise typer.BadParameter(f"format must be one of {', '.join(VALID_FORMATS)}")


def _load_config(config_path: Optional[Path]) -> dict[str, Any]:
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


def _resolved_optional(value: Optional[Path], default: Any) -> Optional[Path]:
    if value is not None:
        return value
    if isinstance(default, str):
        return Path(default)
    if isinstance(default, Path):
        return default
    return None


def _resolved_float(
    cli_value: Optional[float],
    cfg_value: Any,
    *,
    default: float,
) -> float:
    if cli_value is not None:
        return float(cli_value)
    if isinstance(cfg_value, (int, float)):
        return float(cfg_value)
    return default


if __name__ == "__main__":
    app()
