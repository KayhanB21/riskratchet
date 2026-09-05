"""`riskratchet doctor`: setup pre-flight diagnosis.

A user-facing pre-flight: validate the things that would make `check`
fail at boot (paths, baseline, coverage + freshness, git, config,
suppressions) and report each in a short table with a copy-pasteable
remediation when it fails. The point is to turn "my CI is red and I
don't know why" into "doctor says coverage.json is older than my code —
re-run pytest --cov."

Checks only FAIL where the same setup already breaks `check`; anything
merely suspicious is a WARN, so upgrading riskratchet never turns a
previously-green `doctor` red.

Lives outside `cli.py` so the CLI command stays a thin shell; the JSON
envelope is contract-stable and validated against
`schemas/doctor.schema.json`.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from riskratchet._paths import relative_posix
from riskratchet.analysis import iter_python_files
from riskratchet.baseline import BaselineVersionError, load_baseline
from riskratchet.baseline.io import runtime_typescript_identity
from riskratchet.config import invalid_config_values, unknown_config_keys
from riskratchet.coverage import CoverageData, load_coverage
from riskratchet.git import is_shallow_repo
from riskratchet.typescript import _require_tree_sitter, iter_typescript_files

# Bound the overlap walk so `doctor` stays sub-second on a monorepo.
_OVERLAP_FILE_CAP = 2000
_TS_SUFFIXES = (".ts", ".tsx", ".mts", ".cts")
_TS_COVERAGE_COMMAND = "npx vitest run --coverage --coverage.reporter=lcov  # or c8/nyc/jest"


class CheckStatus(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    status: CheckStatus
    summary: str
    remediation: str | None = None


def diagnose(
    *,
    config_dir: Path,
    cfg: Mapping[str, Any],
    paths: list[Path],
    baseline_file: Path,
    coverage_path: Path | None,
    coverage_origin: str = "coverage",
    typescript: bool = False,
    ts_coverage: Sequence[Path] = (),
) -> list[DoctorCheck]:
    """Run every check and return the results in declaration order.

    `paths` should be the already-resolved/anchored scan paths. `cfg` is
    the loaded `[tool.riskratchet]` dict (empty when no config exists).
    `baseline_file` and `coverage_path` are the anchored disk paths the
    rest of riskratchet would use; pass `None` for coverage when the
    user has no coverage configured at all. `coverage_origin` names where
    the path came from (`coverage`, `coverage_map`, `coverage_cache`) so the
    remediation can be specific. `typescript` and `ts_coverage` are the
    resolved `[tool.riskratchet]` values (since 0.3.6): `doctor` diagnoses
    the config, so it takes no flags of its own.

    The coverage file is parsed once and reused by the overlap and
    branch-data checks; those two are skipped when it isn't loadable, and
    when Python coverage is not applicable — TypeScript on and no `.py`
    under the scan paths, the rule `check` applies — so the two commands
    never disagree about whether a project is usable.
    """
    python_files = _scanned_files(paths, config_dir=config_dir, cfg=cfg, language="python")
    if typescript and not python_files:
        coverage_check, data = _coverage_not_applicable(), None
    else:
        coverage_check, data = _check_coverage(coverage_path, source_paths=paths, origin=coverage_origin)
    checks = [
        _check_paths(paths, typescript=typescript),
        _check_baseline(baseline_file),
        coverage_check,
    ]
    if data is not None:
        checks.append(_check_coverage_overlap(data, files=python_files, config_dir=config_dir))
        checks.append(_check_branch_data(data, coverage_path))
    checks.append(_check_git(config_dir))
    checks.append(_check_shallow_clone(config_dir))
    typescript_check = _check_typescript(baseline_file, paths=paths, enabled=typescript)
    if typescript_check is not None:
        checks.append(typescript_check)
    if typescript:
        ts_files = _scanned_files(paths, config_dir=config_dir, cfg=cfg, language="typescript")
        checks.append(_check_ts_coverage(list(ts_coverage), files=ts_files, config_dir=config_dir))
    checks.append(_check_config(cfg))
    checks.append(_check_suppressions(cfg))
    return checks


def summarize(checks: list[DoctorCheck]) -> dict[str, int]:
    """Roll up per-status counts for the JSON envelope."""
    return {
        "passed": sum(1 for c in checks if c.status is CheckStatus.PASS),
        "warned": sum(1 for c in checks if c.status is CheckStatus.WARN),
        "failed": sum(1 for c in checks if c.status is CheckStatus.FAIL),
        "total": len(checks),
    }


def _check_paths(paths: list[Path], *, typescript: bool = False) -> DoctorCheck:
    """The scan paths exist and hold something to score.

    With TypeScript on, `.ts` files count: a TypeScript-only package used to get
    `WARN no .py files` with a remediation about package roots — the wrong fix. With
    it off, `.ts` files under the paths are worth a word, since the switch that would
    score them is one config key away.
    """
    missing = [p for p in paths if not p.exists()]
    if missing:
        names = ", ".join(str(p) for p in missing)
        return DoctorCheck(
            name="paths",
            status=CheckStatus.FAIL,
            summary=f"missing scan paths: {names}",
            remediation="check spelling, or update [tool.riskratchet] paths in pyproject.toml",
        )
    problem = _path_content_problem(paths, typescript=typescript)
    if problem is not None:
        return problem
    return DoctorCheck(
        name="paths",
        status=CheckStatus.PASS,
        summary=", ".join(str(p) for p in paths) or ".",
    )


def _path_content_problem(paths: list[Path], *, typescript: bool) -> DoctorCheck | None:
    """A WARN when a path holds nothing to score, or TypeScript the config does not score."""
    with_typescript = [p for p in paths if _has_typescript_files(p)]
    empty = [p for p in paths if not _has_python_files(p) and p not in with_typescript]
    if empty:
        names = ", ".join(str(p) for p in empty)
        return DoctorCheck(
            name="paths",
            status=CheckStatus.WARN,
            summary=f"no .py{' or .ts' if typescript else ''} files under: {names}",
            remediation="verify the scan path is the package root, not a sibling directory",
        )
    if with_typescript and not typescript:
        names = ", ".join(str(p) for p in with_typescript)
        return DoctorCheck(
            name="paths",
            status=CheckStatus.WARN,
            summary=f"TypeScript files under {names} but typescript is not enabled — they are not scored",
            remediation="[tool.riskratchet] typescript = true  # or pass --typescript",
        )
    return None


def _coverage_not_applicable() -> DoctorCheck:
    """The TypeScript-only tree: `check` neither runs a test command nor asks for a
    report there (0.3.6), so a missing `coverage.json` is not a problem to diagnose.
    The dependent overlap / branch-data rows are skipped with it."""
    return DoctorCheck(
        name="coverage",
        status=CheckStatus.PASS,
        summary="not applicable (no Python files under the scan paths)",
    )


def _check_baseline(baseline_file: Path) -> DoctorCheck:
    if not baseline_file.exists():
        return DoctorCheck(
            name="baseline",
            status=CheckStatus.FAIL,
            summary=f"baseline not found: {baseline_file}",
            remediation="riskratchet baseline",
        )
    dropped: list[int] = []
    try:
        # `cli._load_baseline_or_exit` passes `on_dropped`; without it here, `doctor`
        # reported PASS on a baseline that had silently lost entries — the one command
        # whose job is to notice that.
        baseline = load_baseline(baseline_file, on_dropped=dropped.append)
    except BaselineVersionError as exc:
        # Regenerating would overwrite a newer, still-valid baseline with an older
        # format — the opposite of the fix.
        return DoctorCheck(
            name="baseline",
            status=CheckStatus.FAIL,
            summary=f"baseline is unreadable: {exc}",
            remediation="pip install --upgrade riskratchet",
        )
    except ValueError as exc:
        return DoctorCheck(
            name="baseline",
            status=CheckStatus.FAIL,
            summary=f"baseline is malformed: {exc}",
            remediation="riskratchet baseline  # regenerate from current state",
        )
    if dropped:
        return DoctorCheck(
            name="baseline",
            status=CheckStatus.WARN,
            summary=f"{baseline_file}: {dropped[0]} unreadable entr{'y' if dropped[0] == 1 else 'ies'} "
            f"dropped ({len(baseline.entries)} usable) — those functions are not ratcheted",
            remediation="riskratchet baseline  # regenerate from current state",
        )
    if not baseline.entries:
        # An empty baseline passes every gate, so PASS is the wrong word for it even
        # when the file is well-formed.
        return DoctorCheck(
            name="baseline",
            status=CheckStatus.WARN,
            summary=f"{baseline_file} has no entries — every gate passes",
            remediation="riskratchet baseline  # capture the current state",
        )
    return DoctorCheck(
        name="baseline",
        status=CheckStatus.PASS,
        summary=f"{baseline_file} ({len(baseline.entries)} entries)",
    )


def _check_coverage(
    coverage_path: Path | None,
    *,
    source_paths: list[Path],
    origin: str = "coverage",
) -> tuple[DoctorCheck, CoverageData | None]:
    """Check the coverage file, returning it parsed for the dependent checks.

    Parsing is the point: `exists()` + mtime alone reported PASS on a file that
    made `check` die with a `ValueError`, which is the one situation where a
    doctor is actively harmful.
    """
    if coverage_path is None:
        return (
            DoctorCheck(
                name="coverage",
                status=CheckStatus.WARN,
                summary="no coverage configured (using pessimistic policy)",
                remediation="pytest --cov --cov-branch --cov-report=json:.riskratchet/coverage.json -q",
            ),
            None,
        )
    regenerate = f"pytest --cov --cov-branch --cov-report=json:{coverage_path} -q"
    if not coverage_path.exists():
        # An auto-coverage cache that hasn't been written yet is expected, not broken.
        if origin == "coverage_cache":
            return (
                DoctorCheck(
                    name="coverage",
                    status=CheckStatus.WARN,
                    summary=f"no coverage file yet; auto-coverage will create {coverage_path}",
                    remediation=regenerate,
                ),
                None,
            )
        # Same reasoning for a configured `coverage` path auto-coverage will fill:
        # `config._report_missing_coverage` warns and continues there, so FAILing
        # here would make `doctor` exit 1 on a setup `check` runs happily. When
        # auto-coverage is off there is no substitute and it stays a FAIL.
        if origin == "coverage_auto":
            return (
                DoctorCheck(
                    name="coverage",
                    status=CheckStatus.WARN,
                    summary=f"coverage file not found: {coverage_path}; auto-coverage will substitute",
                    remediation=regenerate,
                ),
                None,
            )
        return (
            DoctorCheck(
                name="coverage",
                status=CheckStatus.FAIL,
                summary=f"coverage file not found: {coverage_path}",
                remediation=regenerate,
            ),
            None,
        )
    try:
        data = load_coverage(coverage_path)
    except ValueError as exc:
        return (
            DoctorCheck(
                name="coverage",
                status=CheckStatus.FAIL,
                summary=f"coverage file is malformed: {exc}",
                remediation=regenerate,
            ),
            None,
        )
    cov_mtime = coverage_path.stat().st_mtime
    newer = _find_newer(source_paths, cov_mtime, suffixes=(".py",))
    if newer is not None:
        return (
            DoctorCheck(
                name="coverage",
                status=CheckStatus.WARN,
                summary=f"coverage older than {newer} (stale)",
                remediation=regenerate,
            ),
            data,
        )
    return (
        DoctorCheck(
            name="coverage",
            status=CheckStatus.PASS,
            summary=f"{coverage_path} (fresh)",
        ),
        data,
    )


def _check_coverage_overlap(
    data: CoverageData,
    *,
    files: list[Path] | None,
    config_dir: Path,
) -> DoctorCheck:
    """Warn when the coverage report barely mentions the files being scanned.

    A coverage.json measured from a different working directory (or covering
    site-packages) resolves nothing, so every function scores as 0% covered
    and risk scores balloon — with no error anywhere. `CoverageData.lookup`
    already tolerates path-format differences, so a miss here is a real miss.
    `files` is the enumerated Python set (`None` when the walk failed).
    """
    if files is None:
        return DoctorCheck(
            name="coverage-overlap",
            status=CheckStatus.WARN,
            summary="could not enumerate scan paths",
        )
    if not files:
        return DoctorCheck(
            name="coverage-overlap", status=CheckStatus.PASS, summary="no scanned files to match"
        )
    hits = sum(1 for path in files if data.lookup(relative_posix(path, config_dir)) is not None)
    if hits == 0:
        return DoctorCheck(
            name="coverage-overlap",
            status=CheckStatus.WARN,
            summary=f"0 of {len(files)} scanned files appear in coverage",
            remediation="coverage measures a different tree — re-run the test command from the project root",
        )
    if hits * 2 < len(files):
        return DoctorCheck(
            name="coverage-overlap",
            status=CheckStatus.WARN,
            summary=f"only {hits} of {len(files)} scanned files appear in coverage",
            remediation="re-run tests over every scanned path, or narrow [tool.riskratchet] paths",
        )
    return DoctorCheck(
        name="coverage-overlap",
        status=CheckStatus.PASS,
        summary=f"{hits} of {len(files)} scanned files present",
    )


def _check_branch_data(data: CoverageData, coverage_path: Path | None) -> DoctorCheck:
    """Warn when coverage carries no branch data.

    `branch_gap` returns 0.0 when `branch_coverage is None`, so 15% of the
    weight silently evaporates and every score comes out *lower* than it
    should — under-reported risk in a risk tool.
    """
    if _has_branch_data(data):
        return DoctorCheck(name="branch-data", status=CheckStatus.PASS, summary="branch coverage present")
    target = coverage_path or Path("coverage.json")
    return DoctorCheck(
        name="branch-data",
        status=CheckStatus.WARN,
        summary="no branch data (branch_gap scores as 0 — 15% of the weight)",
        remediation=f"pytest --cov --cov-branch --cov-report=json:{target} -q",
    )


def _check_shallow_clone(config_dir: Path) -> DoctorCheck:
    """Warn on a shallow clone: `git log --since` sees only HEAD, so churn is 0.

    `actions/checkout` defaults to depth 1, which is the usual way CI ends up
    scoring differently from a locally-generated baseline.
    """
    if is_shallow_repo(config_dir):
        return DoctorCheck(
            name="shallow-clone",
            status=CheckStatus.WARN,
            summary="shallow clone (churn signals score as zero)",
            remediation="git fetch --unshallow  # in CI: actions/checkout with fetch-depth: 0",
        )
    return DoctorCheck(name="shallow-clone", status=CheckStatus.PASS, summary="full history")


def _check_typescript(baseline_file: Path, *, paths: list[Path], enabled: bool = False) -> DoctorCheck | None:
    """Compare the baseline's recorded TypeScript identity against the runtime.

    Fires when config enables TypeScript (0.3.6) or the baseline records a
    TypeScript identity; returns `None` for a Python-only project so nothing new
    appears in the common case. A mismatch already degrades gracefully (0.3.1
    prints the re-baseline command during `check`); reporting it here makes it
    findable before CI does. The absent extra is a FAIL only when config turned
    TypeScript on — every command then exits 2 with the same install hint — and
    stays a WARN when only the baseline mentions TypeScript, as before.
    """
    persisted = _persisted_typescript_identity(baseline_file)
    if persisted is None and not enabled:
        return None
    try:
        # Import it, not just read its metadata: a dist whose import fails is as absent
        # as no dist at all, and `check` would exit 2 on exactly that import.
        _require_tree_sitter()
        runtime = runtime_typescript_identity()
    except Exception:
        return DoctorCheck(
            name="typescript",
            status=CheckStatus.FAIL if enabled else CheckStatus.WARN,
            summary=(
                "typescript = true but the [typescript] extra is not installed"
                if enabled
                else "baseline has TypeScript entries but the [typescript] extra is not installed"
            ),
            remediation="pip install 'riskratchet[typescript]'",
        )
    if persisted is None:
        return DoctorCheck(
            name="typescript",
            status=CheckStatus.PASS,
            summary=f"enabled; scheme {runtime.get('scheme')}, grammar {runtime.get('grammar')}",
        )
    if runtime != persisted:
        target = " ".join(str(p) for p in paths) or "<paths>"
        return DoctorCheck(
            name="typescript",
            status=CheckStatus.WARN,
            summary=(
                f"grammar {runtime.get('grammar')} / scheme {runtime.get('scheme')} "
                f"differs from baseline {persisted.get('grammar')} / scheme {persisted.get('scheme')}"
            ),
            remediation=f"riskratchet baseline {target} --typescript --output {baseline_file}",
        )
    return DoctorCheck(
        name="typescript",
        status=CheckStatus.PASS,
        summary=f"scheme {runtime.get('scheme')}, grammar {runtime.get('grammar')}",
    )


def _check_git(config_dir: Path) -> DoctorCheck:
    try:
        rc = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=config_dir,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return DoctorCheck(
            name="git",
            status=CheckStatus.WARN,
            summary="git not on PATH (churn signals disabled)",
            remediation="install git, or pass --no-git to silence this",
        )
    if rc.returncode != 0:
        return DoctorCheck(
            name="git",
            status=CheckStatus.WARN,
            summary="not a git repo (churn signals disabled)",
            remediation="git init  # or pass --no-git to silence this",
        )
    return DoctorCheck(name="git", status=CheckStatus.PASS, summary="git repo")


def _check_config(cfg: Mapping[str, Any]) -> DoctorCheck:
    if not cfg:
        return DoctorCheck(
            name="config",
            status=CheckStatus.WARN,
            summary="no [tool.riskratchet] in pyproject.toml",
            remediation='add [tool.riskratchet] with at least `paths = ["src"]`',
        )
    # Reuse the real collectors rather than duplicating type rules: `paths = "src"`
    # (a string, not a list) used to pass doctor and fail later. WARN, not FAIL —
    # `config validate` is the strict exit-2 gate; doctor only reports. Both classes
    # are reported together: returning early on an unknown key used to hide every
    # type error behind a single typo, so fixing one revealed the next.
    unknown = unknown_config_keys(cfg)
    problems = invalid_config_values(cfg)
    if unknown or problems:
        return _config_problem_check(unknown, problems)
    return DoctorCheck(name="config", status=CheckStatus.PASS, summary=f"{len(cfg)} key(s)")


def _config_problem_check(unknown: list[str], problems: list[str]) -> DoctorCheck:
    parts = [f"unknown keys: {', '.join(unknown)}"] if unknown else []
    if problems:
        parts.append(f"invalid config: {'; '.join(problems)}")
    return DoctorCheck(
        name="config",
        status=CheckStatus.WARN,
        summary="; ".join(parts),
        # A typo is the likelier cause when the *only* problem is an unrecognized key,
        # so keep pointing at that before reaching for the general validator.
        remediation=(
            "riskratchet config validate  # strict check, exits 2"
            if problems
            else "remove the keys or check for typos (e.g. fail_new_above vs fail_new_abvoe)"
        ),
    )


def _check_suppressions(cfg: Mapping[str, Any]) -> DoctorCheck:
    raw = cfg.get("allow")
    if not raw:
        return DoctorCheck(name="suppressions", status=CheckStatus.PASS, summary="0 patterns")
    if not isinstance(raw, list):
        return DoctorCheck(
            name="suppressions",
            status=CheckStatus.FAIL,
            summary="allow must be a list of strings",
            remediation='allow = ["src/legacy/**"]',
        )
    bad = [p for p in raw if not isinstance(p, str) or not p.strip()]
    if bad:
        return DoctorCheck(
            name="suppressions",
            status=CheckStatus.FAIL,
            summary=f"{len(bad)} invalid pattern(s)",
            remediation="remove empty / non-string entries from [tool.riskratchet] allow",
        )
    return DoctorCheck(
        name="suppressions",
        status=CheckStatus.PASS,
        summary=f"{len(raw)} pattern(s)",
    )


def _has_branch_data(data: CoverageData) -> bool:
    """True when any file payload carries the keys `--cov-branch` produces."""
    for path in data.file_paths:
        payload = data.lookup(path)
        if isinstance(payload, dict) and ("executed_branches" in payload or "missing_branches" in payload):
            return True
    return False


def _persisted_typescript_identity(baseline_file: Path) -> dict[str, Any] | None:
    """The `identity.typescript` block of a readable baseline, else None."""
    if not baseline_file.exists():
        return None
    try:
        persisted = load_baseline(baseline_file).identity.get("typescript")
    except ValueError:
        return None  # already reported by the baseline check
    return persisted if isinstance(persisted, dict) else None


def _check_ts_coverage(reports: list[Path], *, files: list[Path] | None, config_dir: Path) -> DoctorCheck:
    """The TypeScript twin of `_check_coverage` (+ overlap), one row, same branch order.

    Only emitted when TypeScript is enabled. Nothing configured is a WARN, not a
    FAIL: `check` runs without a report and scores every TypeScript function as
    uncovered, which is worth saying but does not break the gate. A configured
    report that is missing or malformed is exactly what makes `check` exit 2, so
    those FAIL with the command that writes one.
    """
    if not reports:
        return DoctorCheck(
            name="ts-coverage",
            status=CheckStatus.WARN,
            summary="no TypeScript coverage configured (TypeScript functions score as uncovered)",
            remediation=(
                f'{_TS_COVERAGE_COMMAND}; then [tool.riskratchet] ts_coverage = ["coverage/lcov.info"]'
            ),
        )
    missing = [p for p in reports if not p.exists()]
    if missing:
        names = ", ".join(str(p) for p in missing)
        return DoctorCheck(
            name="ts-coverage",
            status=CheckStatus.FAIL,
            summary=f"TypeScript coverage report not found: {names}",
            remediation=_TS_COVERAGE_COMMAND,
        )
    from riskratchet.typescript_coverage import load_ts_coverage_files

    try:
        data = load_ts_coverage_files(reports, strict=True)
    except ValueError as exc:
        return DoctorCheck(
            name="ts-coverage",
            status=CheckStatus.FAIL,
            summary=f"TypeScript coverage report is malformed: {exc}",
            remediation=_TS_COVERAGE_COMMAND,
        )
    oldest = min(p.stat().st_mtime for p in reports)
    newer = _find_newer(list(files or []), oldest, suffixes=_TS_SUFFIXES)
    if newer is not None:
        return DoctorCheck(
            name="ts-coverage",
            status=CheckStatus.WARN,
            summary=f"TypeScript coverage older than {newer} (stale)",
            remediation=_TS_COVERAGE_COMMAND,
        )
    names = ", ".join(str(p) for p in reports)
    if files:
        hits = sum(1 for path in files if data.lookup(relative_posix(path, config_dir)) is not None)
        if hits == 0:
            return DoctorCheck(
                name="ts-coverage",
                status=CheckStatus.WARN,
                summary=f"0 of {len(files)} scanned TypeScript files appear in {names}",
                remediation="coverage measures a different tree — run the test command from the project root",
            )
    return DoctorCheck(name="ts-coverage", status=CheckStatus.PASS, summary=f"{names} (fresh)")


def _scanned_files(
    paths: list[Path], *, config_dir: Path, cfg: Mapping[str, Any], language: str
) -> list[Path] | None:
    """The files a scan would reach for one language, honouring `include` / `exclude`.

    `None` when the walk failed (unreadable directory), which the overlap check reports.
    Capped so `doctor` stays sub-second on a monorepo.
    """
    include = cfg.get("include")
    exclude = cfg.get("exclude")
    walk = iter_python_files if language == "python" else iter_typescript_files
    try:
        return walk(
            paths,
            root=config_dir,
            include=list(include) if isinstance(include, list) else [],
            exclude=list(exclude) if isinstance(exclude, list) else [],
        )[:_OVERLAP_FILE_CAP]
    except OSError:
        return None


def _has_python_files(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        return bool(iter_python_files([path], root=path.resolve()))
    except OSError:
        return False


def _has_typescript_files(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        return bool(iter_typescript_files([path], root=path.resolve()))
    except OSError:
        return False


def _find_newer(source_paths: list[Path], cov_mtime: float, *, suffixes: tuple[str, ...]) -> str | None:
    """Return the first file with one of `suffixes` newer than `cov_mtime`, or None."""
    for src in source_paths:
        if not src.exists():
            continue
        if src.is_file() and src.suffix in suffixes and src.stat().st_mtime > cov_mtime:
            return str(src)
        if src.is_dir():
            for suffix in suffixes:
                for candidate in src.rglob(f"*{suffix}"):
                    if candidate.stat().st_mtime > cov_mtime:
                        return str(candidate)
    return None
