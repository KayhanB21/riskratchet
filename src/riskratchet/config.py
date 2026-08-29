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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

if TYPE_CHECKING:
    from riskratchet.diagnostics import Diagnostics

from riskratchet.auto_coverage import (
    DEFAULT_CACHE_PATH,
    DEFAULT_TEST_COMMAND,
    AutoCoverageResult,
    ensure_coverage,
)
from riskratchet.coverage import MissingCoveragePolicy
from riskratchet.git import DEFAULT_CHURN_WINDOW_DAYS, head_sha
from riskratchet.groups import normalize_groups
from riskratchet.redaction import RedactionConfig, resolve_salt
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
    "private_comment",
    "redact_paths",
    "redact_qualnames",
    "redact_salt",
    "test_command",
    "weights",
}

# Value-type groups for `invalid_config_values`. Every name here must also be in
# CONFIG_ALLOWED_KEYS; `test_config.py` asserts that, so adding a key in one place
# and forgetting the other is a test failure rather than a silently unvalidated key.
_NUMBER_KEYS = (
    "fail_above",
    "fail_new_above",
    "fail_regression_above",
    "fail_existing_above",
    "fail_component_regression_above",
)
_BOOL_KEYS = (
    "allow_missing_coverage",
    "component_regression_gate",
    "auto_coverage",
    "redact_paths",
    "redact_qualnames",
    "private_comment",
)


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


def unknown_config_keys(cfg: Mapping[str, Any]) -> list[str]:
    """Return the `[tool.riskratchet]` keys this build does not recognize.

    Deliberately *not* an error on the main commands: an unknown key may come
    from a config written for a newer riskratchet, so it warns and the run
    continues. A known key with a wrong-typed value is the opposite case —
    it can never be right — and is collected by `invalid_config_values`.
    """
    return sorted(set(cfg) - CONFIG_ALLOWED_KEYS)


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
    """Raise `ValueError` on the first problem, if any.

    The strict (exit 2) face of the collectors below, kept so `config
    validate` and `doctor._check_config` read unchanged. The collectors are
    the shared source of truth: the analysis commands route into them too,
    which is what stops `fail_new_above = "50"` from being dropped in
    silence while `config validate` rejects it.
    """
    unknown = unknown_config_keys(cfg)
    if unknown:
        raise ValueError(f"unknown [tool.riskratchet] key(s): {', '.join(unknown)}")
    problems = invalid_config_values(cfg)
    if problems:
        raise ValueError(problems[0])


def invalid_config_values(cfg: Mapping[str, Any]) -> list[str]:
    """Return one message per `[tool.riskratchet]` value this build cannot use.

    Pure and total — it never raises, so both the warning path and the
    exit-2 path can call it. Ordered to match the sequence `_validate_config`
    used to check in, so the first element is the message it used to raise.
    """
    return [
        *_string_list_problems(cfg),
        *_scalar_type_problems(cfg),
        *_bounded_value_problems(cfg),
        *_table_problems(cfg),
    ]


def _string_list_problems(cfg: Mapping[str, Any]) -> list[str]:
    out = []
    for key in ("paths", "include", "exclude", "allow"):
        value = cfg.get(key, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            out.append(f"{key} must be a list of strings.")
    return out


def _scalar_type_problems(cfg: Mapping[str, Any]) -> list[str]:
    out = []
    for key in ("coverage", "baseline", "coverage_cache", "test_command", "redact_salt"):
        if key in cfg and not isinstance(cfg[key], str):
            out.append(f"{key} must be a string.")
    for key in _NUMBER_KEYS:
        if key in cfg and not _is_number(cfg[key]):
            out.append(f"{key} must be a number.")
    for key in _BOOL_KEYS:
        if key in cfg and not isinstance(cfg[key], bool):
            out.append(f"{key} must be a boolean.")
    return out


def _bounded_value_problems(cfg: Mapping[str, Any]) -> list[str]:
    """Range/enum rules, each guarded by its own type check.

    `_scalar_type_problems` used to raise before these ran; collecting means
    they now see wrong-typed values too, and `0 < "50" <= 100` is a
    `TypeError`, not a validation message.
    """
    out = []
    if _is_number(cfg.get("fail_above")) and not (0 < cfg["fail_above"] <= 100):
        out.append("fail_above must be a number in (0, 100].")
    days = cfg.get("churn_window_days")
    if "churn_window_days" in cfg and (not isinstance(days, int) or isinstance(days, bool) or days < 1):
        out.append("churn_window_days must be an integer >= 1.")
    policy = cfg.get("missing_coverage")
    if "missing_coverage" in cfg and (not isinstance(policy, str) or policy not in VALID_MISSING_COVERAGE):
        out.append(f"missing_coverage must be one of {', '.join(VALID_MISSING_COVERAGE)}.")
    return out


def _table_problems(cfg: Mapping[str, Any]) -> list[str]:
    out = []
    if "weights" in cfg:
        if not isinstance(cfg["weights"], dict):
            out.append("[tool.riskratchet.weights] must be a table.")
        else:
            try:
                resolve_weights(cfg["weights"])
            except InvalidWeightsError as exc:
                out.append(str(exc))
    if "groups" in cfg:
        try:
            normalize_groups(cfg["groups"])
        except ValueError as exc:
            out.append(str(exc))
    if "coverage_map" in cfg:
        try:
            _validate_coverage_map(cfg["coverage_map"])
        except ValueError as exc:
            out.append(str(exc))
    return out


def _validate_coverage_map(raw: Any) -> None:
    if not isinstance(raw, dict):
        raise ValueError("[tool.riskratchet.coverage_map] must be a table mapping prefix -> path.")
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("coverage_map keys must be non-empty strings (repo-relative prefixes).")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"coverage_map[{key!r}] must be a non-empty path string.")


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

    Since 0.3.4 the analysis commands run `cli._enforce_config_or_exit` first, so
    in practice the two error branches below are unreachable from them. They stay
    as the boundary's own guarantee: `_resolved_weights` is called from five sites
    and only one of them (`_build_report_or_exit`) sits inside a `ValueError`
    handler, so dropping them would trade a clean exit 2 for a traceback the day
    a new caller forgets to validate.
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


def _record_coverage_diag(
    diagnostics: Diagnostics | None,
    *,
    mode: str,
    source: str,
    path: str | None = None,
    command: str | None = None,
    returncode: int | None = None,
) -> None:
    """Record the resolved coverage source on the collector, if one is present."""
    if diagnostics is not None:
        diagnostics.set_coverage(mode=mode, source=source, path=path, command=command, returncode=returncode)


def _report_missing_coverage(
    path: Path,
    *,
    from_cli: bool,
    allow_missing: bool,
    substitute: Path | None,
) -> None:
    """Refuse, or announce, a coverage file the user named that does not exist.

    A `--coverage` path is an assertion about *this run*, so a missing file can
    never be right: exit 2, the same answer `_ensure_coverage_map_exists` gives
    for a coverage-map shard and `_check_paths_exist` gives for a scan path, and
    the same verdict `doctor._check_coverage` already prints as FAIL. A path from
    `[tool.riskratchet] coverage` is a *default* that auto-coverage may
    legitimately fill on a fresh clone, so that one continues — but it still has
    to say what it used instead.

    Falling through in silence is what made a one-character typo turn a real
    exit-1 regression into "No risk regressions detected", and made `baseline`
    anchor the ratchet to coverage the user never asked for. This runs before the
    test command does, so a wrong path costs a message rather than a full test run.
    """
    fixes = [
        ("Generate coverage at this path:", f"pytest --cov --cov-branch --cov-report=json:{path} -q"),
    ]
    if from_cli and not allow_missing:
        fixes.append(("Let riskratchet generate it instead:", "<command>  # drop --coverage"))
        fixes.append(("Skip the coverage requirement for this run:", "<command> --allow-missing-coverage"))
        typer.secho(
            _format_setup_error(f"riskratchet: coverage file not found: {path}.", fixes),
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    tail = f"using {substitute} instead" if substitute is not None else "continuing without coverage"
    typer.secho(
        _format_setup_error(f"riskratchet: coverage file not found: {path}; {tail}.", fixes),
        fg=typer.colors.YELLOW,
        err=True,
    )


def _resolve_coverage(
    value: Path | None,
    cfg: dict[str, Any],
    *,
    sources: list[Path],
    no_auto_cov: bool,
    required: bool,
    allow_missing: bool,
    config_dir: Path,
    diagnostics: Diagnostics | None = None,
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
        _record_coverage_diag(diagnostics, mode="single", source="explicit", path=str(requested))
        return requested

    auto_enabled = not no_auto_cov and _resolved_bool(True, cfg.get("auto_coverage"), default=True)
    cache_path = _anchor_config_path(
        Path(str(cfg.get("coverage_cache", str(DEFAULT_CACHE_PATH)))), config_dir
    )
    test_command = str(cfg.get("test_command", DEFAULT_TEST_COMMAND))

    if requested is not None:
        _report_missing_coverage(
            requested,
            from_cli=value is not None,
            allow_missing=allow_missing,
            substitute=cache_path if auto_enabled else None,
        )

    result: AutoCoverageResult = ensure_coverage(
        requested=requested if was_configured else None,
        sources=sources,
        cache_path=cache_path,
        test_command=test_command,
        enabled=auto_enabled,
        cwd=config_dir,
    )
    _record_coverage_diag(
        diagnostics,
        mode="single" if result.path is not None else "none",
        source=result.source,
        path=str(result.path) if result.path is not None else None,
        command=result.command,
        returncode=result.returncode,
    )
    if result.path is not None:
        return result.path

    if not required or allow_missing:
        # A missing named path was already reported by `_report_missing_coverage`,
        # which runs before auto-coverage so it fires whether or not the fallback
        # produced anything.
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
    """Fail when a coverage-map path is missing and the policy forbids it.

    When `allow_missing`, this is a no-op: the loader skips unusable shards and
    warns from `cli._coverage_shard_warn`, which fires where the shard is
    actually dropped and covers malformed files too — not just absent ones.
    Warning here as well would double-report the same shard.
    """
    if allow_missing:
        return
    missing = [(prefix, path) for prefix, path in coverage_map.items() if not path.exists()]
    if not missing:
        return
    for prefix, path in missing:
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
    raise typer.Exit(code=2)


def _ensure_ts_coverage_exists(
    paths: Sequence[Path] | None,
    *,
    allow_missing: bool,
) -> None:
    """Fail when a `--ts-coverage` report the user named does not exist.

    The TypeScript counterpart of `_report_missing_coverage`, and the same rule:
    a report named on the command line is an assertion about this run. Without this,
    `typescript_engine` reported the miss through `on_error` and carried on with an
    empty coverage view — which under `missing_coverage = skip` dropped every
    TypeScript function and still exited 0.
    """
    if allow_missing or not paths:
        return
    missing = [path for path in paths if not Path(path).exists()]
    if not missing:
        return
    for path in missing:
        typer.secho(
            _format_setup_error(
                f"riskratchet: TypeScript coverage report not found: {path}.",
                [
                    ("Generate it with your test runner:", "npx vitest run --coverage  # or nyc/c8/jest"),
                    ("Skip the coverage requirement for this run:", "<command> --allow-missing-coverage"),
                ],
            ),
            fg=typer.colors.RED,
            err=True,
        )
    raise typer.Exit(code=2)


@dataclass(frozen=True)
class GateSettings:
    """Everything `[tool.riskratchet]` contributes to a gating run, already resolved.

    The CLI resolves these inline, one command at a time. This exists so a *second*
    entry point does not have to reproduce that resolution from memory: before 0.3.5
    the pytest plugin read no config at all, so a repo that had tightened
    `fail_regression_above` to 1 still got the plugin's hardcoded 5, custom `weights`
    were ignored (making every score incomparable with the baseline the CLI wrote),
    and `paths = ["lib"]` left the plugin scanning a `src` that did not exist.

    `tests/test_pytest_plugin.py::test_the_plugin_and_the_cli_reach_the_same_verdict`
    is the trip-wire: it fails if this and the CLI ever disagree again.
    """

    paths: list[Path]
    include: list[str]
    exclude: list[str]
    allow: list[str]
    weights: dict[str, float] | None
    groups: dict[str, tuple[str, ...]]
    churn_days: int
    missing_coverage: MissingCoveragePolicy
    fail_new_above: float
    fail_regression_above: float
    fail_existing_above: float | None
    fail_component_regression_above: float
    component_regression_gate: bool


def resolve_gate_settings(
    cfg: dict[str, Any],
    config_dir: Path,
    *,
    paths: list[Path] | None = None,
    fail_new_above: float | None = None,
    fail_regression_above: float | None = None,
    fail_existing_above: float | None = None,
    fail_component_regression_above: float | None = None,
    component_regression_gate: bool = True,
    churn_days: int | None = None,
) -> GateSettings:
    """Resolve config into gate settings, with any explicitly-passed override winning.

    Every override is `None`-defaulted on purpose. An option whose default is a real
    value — the plugin's old `50.0`, `5.0`, `"src"` — cannot be told apart from the
    user passing that same value, so config could never win. That is why the plugin
    silently ignored `[tool.riskratchet]` rather than merely deprioritising it.
    """
    return GateSettings(
        paths=_resolved_paths(paths, cfg, config_dir),
        include=list(cfg.get("include", [])),
        exclude=list(cfg.get("exclude", [])),
        allow=list(cfg.get("allow", [])),
        weights=_resolved_weights(cfg),
        groups=_resolved_groups(cfg),
        churn_days=_resolved_churn_days(churn_days, cfg),
        missing_coverage=_resolved_missing_coverage(None, cfg),
        fail_new_above=_resolved_float(fail_new_above, cfg.get("fail_new_above"), default=50.0),
        fail_regression_above=_resolved_float(
            fail_regression_above, cfg.get("fail_regression_above"), default=5.0
        ),
        fail_existing_above=_resolved_optional_float(fail_existing_above, cfg.get("fail_existing_above")),
        fail_component_regression_above=_resolved_float(
            fail_component_regression_above, cfg.get("fail_component_regression_above"), default=15.0
        ),
        component_regression_gate=(
            component_regression_gate
            and _resolved_bool(True, cfg.get("component_regression_gate"), default=True)
        ),
    )


def discover_config(config_path: Path | None = None) -> tuple[dict[str, Any], Path]:
    """Public wrapper over the upward config walk, for non-CLI entry points."""
    return _discover_config(config_path)


def _secho_warning(message: str) -> None:
    typer.secho(message, fg=typer.colors.YELLOW, err=True)


def _env_var(name: str) -> str | None:
    import os

    value = os.environ.get(name)
    return value or None


def resolve_redaction(
    *,
    redact_paths: bool,
    redact_qualnames: bool,
    private_comment: bool,
    redact_salt: str | None,
    cfg: Mapping[str, Any],
    config_dir: Path,
    warn: Callable[[str], None] | None = None,
) -> RedactionConfig:
    """Build a RedactionConfig from CLI flags, config, and the salt sources.

    `--private-comment` is a preset: it forces both path and qualname redaction
    and suppresses source links for PR comments. When redaction is active but no
    explicit / env / config / git-derived salt exists, warn once that unsalted
    hashes are guessable.

    Lives here rather than in `cli` so the pytest plugin can reach it without
    importing the CLI: before 0.3.5 the plugin printed raw paths and qualnames into
    CI logs for repos that had asked for redaction in config, because this function
    was on the other side of that wall. `warn` lets a caller without typer route the
    unsalted notice into its own reporter.
    """
    say = warn or _secho_warning
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
        repo = _env_var("GITHUB_REPOSITORY")
        sha = _env_var("GITHUB_SHA")
        if repo and sha:
            return f"{repo}@{sha}"
        return head_sha(config_dir)

    resolution = resolve_salt(redact_salt, cfg.get("redact_salt"), auto=_auto_salt)
    if (rp or rq) and resolution.source == "none":
        say(
            "warning: redacting without a salt; hashes are guessable from known paths. "
            "Set --redact-salt or RISKRATCHET_REDACT_SALT for stronger redaction."
        )
    return RedactionConfig(
        redact_paths=rp,
        redact_qualnames=rq,
        suppress_links=pc,
        salt=resolution.salt,
    )


def _resolved_bool(cli_value: bool, cfg_value: Any, *, default: bool = False) -> bool:
    if cli_value != default:
        return cli_value
    if isinstance(cfg_value, bool):
        return cfg_value
    return default
