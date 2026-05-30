"""`riskratchet doctor`: six-check setup diagnosis.

A user-facing pre-flight: validate the things that would make `check`
fail at boot (paths, baseline, coverage + freshness, git, config,
suppressions) and report each in a short table with a copy-pasteable
remediation when it fails. The point is to turn "my CI is red and I
don't know why" into "doctor says coverage.json is older than my code —
re-run pytest --cov."

Lives outside `cli.py` so the CLI command stays a thin shell; the JSON
envelope is contract-stable and validated against
`schemas/doctor.schema.json`.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from riskratchet.analysis import iter_python_files
from riskratchet.baseline import load_baseline
from riskratchet.config import CONFIG_ALLOWED_KEYS

CHECK_NAMES = ("paths", "baseline", "coverage", "git", "config", "suppressions")


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
) -> list[DoctorCheck]:
    """Run all six checks and return their results in declaration order.

    `paths` should be the already-resolved/anchored scan paths. `cfg` is
    the loaded `[tool.riskratchet]` dict (empty when no config exists).
    `baseline_file` and `coverage_path` are the anchored disk paths the
    rest of riskratchet would use; pass `None` for coverage when the
    user has no coverage configured at all.
    """
    return [
        _check_paths(paths),
        _check_baseline(baseline_file),
        _check_coverage(coverage_path, source_paths=paths),
        _check_git(config_dir),
        _check_config(cfg),
        _check_suppressions(cfg),
    ]


def summarize(checks: list[DoctorCheck]) -> dict[str, int]:
    """Roll up per-status counts for the JSON envelope."""
    return {
        "passed": sum(1 for c in checks if c.status is CheckStatus.PASS),
        "warned": sum(1 for c in checks if c.status is CheckStatus.WARN),
        "failed": sum(1 for c in checks if c.status is CheckStatus.FAIL),
        "total": len(checks),
    }


def _check_paths(paths: list[Path]) -> DoctorCheck:
    missing = [p for p in paths if not p.exists()]
    if missing:
        names = ", ".join(str(p) for p in missing)
        return DoctorCheck(
            name="paths",
            status=CheckStatus.FAIL,
            summary=f"missing scan paths: {names}",
            remediation="check spelling, or update [tool.riskratchet] paths in pyproject.toml",
        )
    empty = [p for p in paths if not _has_python_files(p)]
    if empty:
        names = ", ".join(str(p) for p in empty)
        return DoctorCheck(
            name="paths",
            status=CheckStatus.WARN,
            summary=f"no .py files under: {names}",
            remediation="verify the scan path is the package root, not a sibling directory",
        )
    return DoctorCheck(
        name="paths",
        status=CheckStatus.PASS,
        summary=", ".join(str(p) for p in paths) or ".",
    )


def _check_baseline(baseline_file: Path) -> DoctorCheck:
    if not baseline_file.exists():
        return DoctorCheck(
            name="baseline",
            status=CheckStatus.FAIL,
            summary=f"baseline not found: {baseline_file}",
            remediation="riskratchet baseline",
        )
    try:
        baseline = load_baseline(baseline_file)
    except ValueError as exc:
        return DoctorCheck(
            name="baseline",
            status=CheckStatus.FAIL,
            summary=f"baseline is malformed: {exc}",
            remediation="riskratchet baseline  # regenerate from current state",
        )
    return DoctorCheck(
        name="baseline",
        status=CheckStatus.PASS,
        summary=f"{baseline_file} ({len(baseline.entries)} entries)",
    )


def _check_coverage(coverage_path: Path | None, *, source_paths: list[Path]) -> DoctorCheck:
    if coverage_path is None:
        return DoctorCheck(
            name="coverage",
            status=CheckStatus.WARN,
            summary="no coverage configured (using pessimistic policy)",
            remediation="pytest --cov --cov-branch --cov-report=json:.riskratchet/coverage.json -q",
        )
    if not coverage_path.exists():
        return DoctorCheck(
            name="coverage",
            status=CheckStatus.FAIL,
            summary=f"coverage file not found: {coverage_path}",
            remediation=f"pytest --cov --cov-branch --cov-report=json:{coverage_path} -q",
        )
    cov_mtime = coverage_path.stat().st_mtime
    newer = _find_newer_py(source_paths, cov_mtime)
    if newer is not None:
        return DoctorCheck(
            name="coverage",
            status=CheckStatus.WARN,
            summary=f"coverage older than {newer} (stale)",
            remediation=f"pytest --cov --cov-branch --cov-report=json:{coverage_path} -q",
        )
    return DoctorCheck(
        name="coverage",
        status=CheckStatus.PASS,
        summary=f"{coverage_path} (fresh)",
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
    unknown = sorted(set(cfg) - CONFIG_ALLOWED_KEYS)
    if unknown:
        return DoctorCheck(
            name="config",
            status=CheckStatus.WARN,
            summary=f"unknown keys: {', '.join(unknown)}",
            remediation="remove the keys or check for typos (e.g. fail_new_above vs fail_new_abvoe)",
        )
    return DoctorCheck(name="config", status=CheckStatus.PASS, summary=f"{len(cfg)} key(s)")


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


def _has_python_files(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        return bool(iter_python_files([path], root=path.resolve()))
    except OSError:
        return False


def _find_newer_py(source_paths: list[Path], cov_mtime: float) -> str | None:
    """Return the first .py file newer than `cov_mtime`, or None."""
    for src in source_paths:
        if not src.exists():
            continue
        if src.is_file() and src.suffix == ".py" and src.stat().st_mtime > cov_mtime:
            return str(src)
        if src.is_dir():
            for py in src.rglob("*.py"):
                if py.stat().st_mtime > cov_mtime:
                    return str(py)
    return None
