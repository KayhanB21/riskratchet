"""Configuration discovery, validation, and value resolution.

This module owns the `[tool.riskratchet]` concern that used to live inline
in `cli.py`: finding the right `pyproject.toml`, validating it, anchoring
its relative paths, and turning the merged config + CLI flags into the
concrete values the commands feed to `analyze` / `compare` / `diff`.

Path-resolution contract:
- CLI positional paths and the implicit no-arg default are interpreted
  relative to the current working directory (so shell tab-completion and
  "scan here" behave as typed).
- Config-declared paths (`paths`, `coverage`, `coverage_map`,
  `coverage_cache`, `baseline`) are anchored to the directory of the
  discovered config file, so a run from a nested package directory
  resolves them against the project root.
- The auto-coverage test command runs from the config directory, and
  report paths are made relative to it.

`cli.py` stays a thin shell over these helpers (AGENTS.md: business logic
lives outside `cli.py`).
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import typer

from riskratchet.auto_coverage import (
    DEFAULT_CACHE_PATH,
    DEFAULT_TEST_COMMAND,
    AutoCoverageResult,
    ensure_coverage,
)
from riskratchet.coverage import MissingCoveragePolicy
from riskratchet.git import DEFAULT_CHURN_WINDOW_DAYS
from riskratchet.groups import normalize_groups
from riskratchet.scoring import DEFAULT_WEIGHTS, InvalidWeightsError, resolve_weights

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[import-not-found]

VALID_MISSING_COVERAGE = tuple(policy.value for policy in MissingCoveragePolicy)
CONFIG_SCHEMA_URL = "https://github.com/KayhanB21/riskratchet/schemas/config.schema.json"


def _format_setup_error(headline: str, fixes: list[tuple[str, str]]) -> str:
    """Build a multi-line stderr message: headline + numbered remediations.

    Each fix is `(description, command)`. The command is rendered on its own
    indented line so it is copy-pasteable. Used for first-failure messages
    (missing coverage, missing baseline, missing scan path) so every setup
    error tells the user the exact command to run next.
    """
    lines = [headline, "", "Fix one of:"]
    for index, (desc, command) in enumerate(fixes, start=1):
        lines.append(f"  {index}. {desc}")
        lines.append(f"       {command}")
    return "\n".join(lines)


CONFIG_ALLOWED_KEYS = {
    "allow",
    "allow_missing_coverage",
    "auto_coverage",
    "baseline",
    "churn_window_days",
    "component_regression_gate",
    "coverage",
    "coverage_cache",
    "coverage_map",
    "exclude",
    "fail_above",
    "fail_component_regression_above",
    "fail_existing_above",
    "fail_new_above",
    "fail_regression_above",
    "groups",
    "include",
    "missing_coverage",
    "paths",
    "test_command",
    "weights",
}


def _discover_config(config_path: Path | None) -> tuple[dict[str, Any], Path]:
    """Resolve the config section and the directory it lives in.

    With an explicit `--config`, load that file; its parent is the config
    directory. Otherwise walk upward from the current directory for the
    nearest `pyproject.toml` that defines `[tool.riskratchet]` and anchor
    there. When no such ancestor exists, fall back silently to the current
    directory with an empty config.

    The config directory is what relative `paths` / `coverage` / `baseline`
    values are resolved against, so running from a nested package directory
    produces the same result as running from the project root.
    """
    if config_path is not None:
        return _load_config(config_path), config_path.resolve().parent
    cwd = Path.cwd().resolve()
    for directory in (cwd, *cwd.parents):
        section = _riskratchet_section(directory / "pyproject.toml")
        if section is not None:
            return section, directory
    return {}, cwd


def _riskratchet_section(path: Path) -> dict[str, Any] | None:
    """Return the `[tool.riskratchet]` table if `path` defines one, else None.

    A `pyproject.toml` that exists but fails to parse warns on stderr and is
    skipped, so a broken file does not crash discovery — but, unlike a silent
    skip, the user sees why their config was not picked up instead of
    riskratchet quietly walking past it to an ancestor's config.
    """
    if not path.is_file():
        return None
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        typer.secho(
            f"warning: could not parse {path}: {exc}; skipping it during config discovery.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        return None
    tool = raw.get("tool")
    if not isinstance(tool, dict):
        return None
    section = tool.get("riskratchet")
    return section if isinstance(section, dict) else None


def _warn_unknown_config_keys(cfg: Mapping[str, Any]) -> None:
    """Warn (on stderr) about unrecognized `[tool.riskratchet]` keys.

    The main commands tolerate unknown keys so a config written for a newer
    riskratchet still runs, but a typo like `fail_new_abvoe` silently
    disabling a policy is a real trap, so we surface it. `config validate`
    is the strict (exit 2) gate for anyone who wants to enforce it in CI.
    """
    unknown = sorted(set(cfg) - CONFIG_ALLOWED_KEYS)
    if unknown:
        typer.secho(
            f"warning: ignoring unknown [tool.riskratchet] key(s): {', '.join(unknown)}",
            fg=typer.colors.YELLOW,
            err=True,
        )


def _anchor_config_path(path: Path, config_dir: Path) -> Path:
    """Resolve a config-sourced relative path against the config directory.

    When the config lives in the current directory (the common case) the path
    is left relative, so diagnostics and output read naturally (`src/m.py`,
    not an absolute path). Only when config was discovered in an ancestor
    directory is the path rewritten to absolute, so a nested-directory run
    resolves config paths against the project root rather than the cwd.
    """
    if path.is_absolute() or config_dir == Path.cwd().resolve():
        return path
    return config_dir / path


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


def _load_config_strict(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        return {}
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"could not read {config_path}: {exc}") from exc
    tool = raw.get("tool", {})
    if not isinstance(tool, dict):
        raise ValueError("[tool] must be a table.")
    section = tool.get("riskratchet", {})
    if not isinstance(section, dict):
        raise ValueError("[tool.riskratchet] must be a table.")
    _validate_config(section)
    return section


def _validate_config(cfg: dict[str, Any]) -> None:
    unknown = sorted(set(cfg) - CONFIG_ALLOWED_KEYS)
    if unknown:
        raise ValueError(f"unknown [tool.riskratchet] key(s): {', '.join(unknown)}")
    _validate_string_list(cfg, "paths")
    _validate_string_list(cfg, "include")
    _validate_string_list(cfg, "exclude")
    _validate_string_list(cfg, "allow")
    for key in ("coverage", "baseline", "coverage_cache", "test_command"):
        if key in cfg and not isinstance(cfg[key], str):
            raise ValueError(f"{key} must be a string.")
    for key in (
        "fail_above",
        "fail_new_above",
        "fail_regression_above",
        "fail_existing_above",
        "fail_component_regression_above",
    ):
        if key in cfg and not _is_number(cfg[key]):
            raise ValueError(f"{key} must be a number.")
    if "fail_above" in cfg:
        value = cfg["fail_above"]
        if not (0 < value <= 100):
            raise ValueError("fail_above must be a number in (0, 100].")
    for key in ("allow_missing_coverage", "component_regression_gate", "auto_coverage"):
        if key in cfg and not isinstance(cfg[key], bool):
            raise ValueError(f"{key} must be a boolean.")
    if "churn_window_days" in cfg:
        value = cfg["churn_window_days"]
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValueError("churn_window_days must be an integer >= 1.")
    if "missing_coverage" in cfg:
        value = cfg["missing_coverage"]
        if not isinstance(value, str) or value not in VALID_MISSING_COVERAGE:
            raise ValueError(f"missing_coverage must be one of {', '.join(VALID_MISSING_COVERAGE)}.")
    if "weights" in cfg:
        if not isinstance(cfg["weights"], dict):
            raise ValueError("[tool.riskratchet.weights] must be a table.")
        try:
            resolve_weights(cfg["weights"])
        except InvalidWeightsError as exc:
            raise ValueError(str(exc)) from exc
    if "groups" in cfg:
        normalize_groups(cfg["groups"])
    if "coverage_map" in cfg:
        _validate_coverage_map(cfg["coverage_map"])


def _validate_coverage_map(raw: Any) -> None:
    if not isinstance(raw, dict):
        raise ValueError("[tool.riskratchet.coverage_map] must be a table mapping prefix -> path.")
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("coverage_map keys must be non-empty strings (repo-relative prefixes).")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"coverage_map[{key!r}] must be a non-empty path string.")


def _validate_string_list(cfg: dict[str, Any], key: str) -> None:
    if key not in cfg:
        return
    value = cfg[key]
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a list of strings.")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _resolved_config_payload(cfg: dict[str, Any], config_dir: Path) -> dict[str, Any]:
    groups = _resolved_groups(cfg)
    raw_map = cfg.get("coverage_map")
    coverage_map_payload: dict[str, str] = {}
    if isinstance(raw_map, dict):
        coverage_map_payload = {str(prefix): str(path) for prefix, path in raw_map.items()}
    return {
        "paths": [str(path) for path in _resolved_paths([], cfg, config_dir)],
        "coverage": cfg.get("coverage"),
        "coverage_map": coverage_map_payload,
        "baseline": cfg.get("baseline", ".riskratchet.json"),
        "fail_above": _resolved_optional_float(None, cfg.get("fail_above")),
        "fail_new_above": _resolved_float(None, cfg.get("fail_new_above"), default=50.0),
        "fail_regression_above": _resolved_float(None, cfg.get("fail_regression_above"), default=5.0),
        "fail_existing_above": _resolved_optional_float(None, cfg.get("fail_existing_above")),
        "fail_component_regression_above": _resolved_float(
            None, cfg.get("fail_component_regression_above"), default=15.0
        ),
        "component_regression_gate": _resolved_bool(True, cfg.get("component_regression_gate"), default=True),
        "allow_missing_coverage": _resolved_bool(False, cfg.get("allow_missing_coverage")),
        "auto_coverage": _resolved_bool(True, cfg.get("auto_coverage"), default=True),
        "coverage_cache": cfg.get("coverage_cache", str(DEFAULT_CACHE_PATH)),
        "test_command": cfg.get("test_command", DEFAULT_TEST_COMMAND),
        "missing_coverage": _resolved_missing_coverage(None, cfg).value,
        "churn_window_days": _resolved_churn_days(None, cfg),
        "include": cfg.get("include", []),
        "exclude": cfg.get("exclude", []),
        "allow": cfg.get("allow", []),
        "weights": _resolved_weights(cfg) or DEFAULT_WEIGHTS,
        "groups": {name: list(prefixes) for name, prefixes in groups.items()},
    }


def _resolved_weights(cfg: dict[str, Any]) -> dict[str, float] | None:
    """Pull `[tool.riskratchet.weights]` out of config, exiting on invalid input.

    Returning `None` (no table or empty table) lets `engine.analyze` use its
    default weights without an extra branch in each command.
    """
    raw = cfg.get("weights")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        typer.secho(
            "config error: [tool.riskratchet.weights] must be a table.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    if not raw:
        return None
    try:
        return resolve_weights(raw)
    except InvalidWeightsError as exc:
        typer.secho(f"config error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc


def _resolved_groups(cfg: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    try:
        return normalize_groups(cfg.get("groups"))
    except ValueError as exc:
        typer.secho(f"config error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc


def _resolved_missing_coverage(value: str | None, cfg: dict[str, Any]) -> MissingCoveragePolicy:
    raw = value if value is not None else cfg.get("missing_coverage", MissingCoveragePolicy.PESSIMISTIC.value)
    if not isinstance(raw, str) or raw not in VALID_MISSING_COVERAGE:
        typer.secho(
            f"config error: missing coverage policy must be one of {', '.join(VALID_MISSING_COVERAGE)}.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    return MissingCoveragePolicy(raw)


def _parse_coverage_map_flag(values: list[str]) -> dict[str, Path]:
    """Parse repeatable `--coverage-map prefix=path` entries.

    Empty input returns `{}`. Duplicate prefixes raise — they are almost
    certainly a typo, and silently letting the later value win would be a
    surprising loss.
    """
    out: dict[str, Path] = {}
    for raw in values:
        if "=" not in raw:
            raise typer.BadParameter(f"--coverage-map expects prefix=path, got {raw!r}")
        prefix, _, path_str = raw.partition("=")
        prefix = prefix.strip()
        path_str = path_str.strip()
        if not prefix or not path_str:
            raise typer.BadParameter(f"--coverage-map expects prefix=path, got {raw!r}")
        if prefix in out:
            raise typer.BadParameter(f"--coverage-map prefix {prefix!r} given more than once")
        out[prefix] = Path(path_str)
    return out


def _resolved_coverage_map(
    cli_value: list[str] | None,
    cfg: dict[str, Any],
    config_dir: Path,
) -> dict[str, Path]:
    if cli_value:
        return _parse_coverage_map_flag(cli_value)
    raw = cfg.get("coverage_map")
    if not isinstance(raw, dict) or not raw:
        return {}
    try:
        _validate_coverage_map(raw)
    except ValueError as exc:
        typer.secho(f"config error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    return {str(prefix): _anchor_config_path(Path(str(path)), config_dir) for prefix, path in raw.items()}


def _resolved_paths(
    paths: list[Path] | None,
    cfg: dict[str, Any],
    config_dir: Path,
) -> list[Path]:
    """Resolve the scan-target paths, anchoring config-sourced ones.

    Pure resolution: returns the paths, never exits. Callers that need to
    fail fast on a missing path (scan / check / baseline / diff) call
    `cli._check_paths_exist` after resolving. Inspection-only callers
    (`config show`) skip that check by design.
    """
    if paths:
        # CLI paths are interpreted relative to the current directory.
        return paths
    configured = cfg.get("paths")
    if isinstance(configured, list) and configured:
        return [_anchor_config_path(Path(p), config_dir) for p in configured]
    # No paths given anywhere: scan the current directory, not the whole
    # project. The implicit default follows the same cwd-relative rule as an
    # explicit CLI path, so a no-arg run in a subdirectory stays scoped to it.
    return [Path(".")]


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
    config_dir: Path,
) -> Path | None:
    """Resolve which coverage JSON to use, generating one via tests if needed.

    Precedence: an explicit existing `--coverage` path wins; then the
    configured `coverage` path if it exists; then the auto-coverage cache
    (regenerated by running the configured test command when stale). If
    everything fails and the command requires coverage, exit with code 2
    unless `--allow-missing-coverage` was set. Config-sourced paths (the
    configured `coverage` and `coverage_cache`) anchor to `config_dir`; an
    explicit `--coverage` stays relative to the current directory.
    """
    requested, was_configured = _coverage_candidate(value, cfg.get("coverage"))
    if value is None and requested is not None:
        requested = _anchor_config_path(requested, config_dir)
    if requested is not None and requested.exists():
        return requested

    auto_enabled = not no_auto_cov and _resolved_bool(True, cfg.get("auto_coverage"), default=True)
    cache_path = _anchor_config_path(
        Path(str(cfg.get("coverage_cache", str(DEFAULT_CACHE_PATH)))), config_dir
    )
    test_command = str(cfg.get("test_command", DEFAULT_TEST_COMMAND))

    result: AutoCoverageResult = ensure_coverage(
        requested=requested if was_configured else None,
        sources=sources,
        cache_path=cache_path,
        test_command=test_command,
        enabled=auto_enabled,
        cwd=config_dir,
    )
    if result.path is not None:
        return result.path

    if not required or allow_missing:
        if requested is not None and value is not None and not requested.exists():
            typer.secho(
                _format_setup_error(
                    f"riskratchet: coverage file not found: {requested}; continuing without coverage.",
                    [
                        (
                            "Generate coverage at this path:",
                            f"pytest --cov --cov-branch --cov-report=json:{requested} -q",
                        ),
                    ],
                ),
                fg=typer.colors.YELLOW,
                err=True,
            )
        return None

    resolved_test_command = test_command.format(output=str(cache_path))
    typer.secho(
        _format_setup_error(
            (
                f"riskratchet: coverage data is required but none could be produced. "
                f"Tried --coverage ({requested}), auto-coverage cache ({cache_path}), "
                f"and `{resolved_test_command}`."
            ),
            [
                (
                    "Generate coverage manually:",
                    f"pytest --cov --cov-branch --cov-report=json:{cache_path} -q",
                ),
                ("Skip the coverage requirement for this run:", "<command> --allow-missing-coverage"),
                ("Disable auto-coverage and supply a path:", "<command> --no-auto-cov --coverage <path>"),
            ],
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


def _resolved_churn_days(cli_value: int | None, cfg: dict[str, Any]) -> int:
    if cli_value is not None:
        if cli_value < 1:
            raise typer.BadParameter("--churn-days must be >= 1")
        return cli_value
    cfg_value = cfg.get("churn_window_days")
    if isinstance(cfg_value, int) and not isinstance(cfg_value, bool):
        if cfg_value < 1:
            raise typer.BadParameter("[tool.riskratchet] churn_window_days must be >= 1")
        return cfg_value
    return DEFAULT_CHURN_WINDOW_DAYS


def _ensure_coverage_map_exists(
    coverage_map: Mapping[str, Path],
    *,
    allow_missing: bool,
) -> None:
    """Verify every coverage-map path exists; warn or fail depending on policy."""
    missing = [(prefix, path) for prefix, path in coverage_map.items() if not path.exists()]
    if not missing:
        return
    for prefix, path in missing:
        if allow_missing:
            typer.secho(
                _format_setup_error(
                    f"riskratchet: coverage-map[{prefix}] file not found: {path}; treating as no coverage.",
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
        else:
            typer.secho(
                _format_setup_error(
                    f"riskratchet: coverage-map[{prefix}] file not found: {path}.",
                    [
                        (
                            "Generate coverage at this path:",
                            f"pytest --cov --cov-branch --cov-report=json:{path} -q",
                        ),
                        ("Skip the coverage requirement for this run:", "<command> --allow-missing-coverage"),
                    ],
                ),
                fg=typer.colors.RED,
                err=True,
            )
    if not allow_missing:
        raise typer.Exit(code=2)


def _resolved_bool(cli_value: bool, cfg_value: Any, *, default: bool = False) -> bool:
    if cli_value != default:
        return cli_value
    if isinstance(cfg_value, bool):
        return cfg_value
    return default
