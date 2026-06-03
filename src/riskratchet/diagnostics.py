"""Structured run diagnostics for `--verbose` / `--debug-json` (P11, 0.2.9).

A `Diagnostics` collector accumulates a small, fixed set of categories about
how a run resolved its inputs — coverage source, git/churn, filters, the
analysis tallies, and (for `check`/`diff`) the baseline. It renders two ways:

- `to_lines()` — human-readable `riskratchet: ...` lines for `--verbose`.
- `to_envelope()` — a schema-versioned JSON object for `--debug-json`.

Both go to **stderr** (or, for `--debug-json PATH`, a file). Stdout stays
payload-only regardless. The category set is deliberately small (the roadmap's
"smallest useful set"); add more only when a real debugging session needs them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from riskratchet.reporting import DEBUG_SCHEMA_URL

DEBUG_OUTPUT_VERSION = 1
"""Schema version of the `--debug-json` envelope. Its own contract, bumped
independently of the native payload `OUTPUT_VERSION`."""


@dataclass
class Diagnostics:
    """Mutable per-run collector. Populated by the CLI as a run resolves.

    Each `set_*` method is called at most once. Unset categories render as
    `null` in the JSON envelope and are skipped in `--verbose` lines.
    """

    command: str
    coverage: dict[str, Any] | None = None
    git: dict[str, Any] | None = None
    filters: dict[str, Any] | None = None
    analysis: dict[str, Any] | None = None
    baseline: dict[str, Any] | None = None

    def set_coverage(
        self,
        *,
        mode: str,
        source: str,
        path: str | None = None,
        coverage_map: dict[str, str] | None = None,
        command: str | None = None,
        returncode: int | None = None,
    ) -> None:
        self.coverage = {
            "mode": mode,
            "source": source,
            "path": path,
            "map": coverage_map,
            "command": command or None,
            "returncode": returncode,
        }

    def set_git(self, *, enabled: bool, churn_window_days: int, repo_present: bool) -> None:
        self.git = {
            "enabled": enabled,
            "churn_window_days": churn_window_days,
            "repo_present": repo_present,
        }

    def set_filters(
        self,
        *,
        include: list[str],
        exclude: list[str],
        allow: list[str],
        suppressed_functions: int,
    ) -> None:
        self.filters = {
            "include": include,
            "exclude": exclude,
            "allow": allow,
            "suppressed_functions": suppressed_functions,
        }

    def set_analysis(
        self,
        *,
        coverage_status: str,
        analyzed_functions: int,
        reported_functions: int,
        skipped_missing_coverage: int,
    ) -> None:
        self.analysis = {
            "coverage_status": coverage_status,
            "analyzed_functions": analyzed_functions,
            "reported_functions": reported_functions,
            "skipped_missing_coverage": skipped_missing_coverage,
        }

    def set_baseline(self, *, path: str, present: bool, entry_count: int | None) -> None:
        self.baseline = {
            "path": path,
            "present": present,
            "entry_count": entry_count,
        }

    def to_envelope(self) -> dict[str, Any]:
        return {
            "$schema": DEBUG_SCHEMA_URL,
            "version": DEBUG_OUTPUT_VERSION,
            "command": self.command,
            "diagnostics": {
                "coverage": self.coverage,
                "git": self.git,
                "filters": self.filters,
                "analysis": self.analysis,
                "baseline": self.baseline,
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_envelope(), indent=2)

    def to_lines(self) -> list[str]:
        lines = [f"riskratchet: diagnostics for command={self.command}"]
        if self.coverage is not None:
            cov = self.coverage
            detail = f"mode={cov['mode']} source={cov['source']}"
            if cov.get("path"):
                detail += f" path={cov['path']}"
            if cov.get("map"):
                detail += " map=" + ",".join(f"{k}:{v}" for k, v in cov["map"].items())
            if cov.get("command"):
                detail += f" command='{cov['command']}' returncode={cov['returncode']}"
            lines.append(f"riskratchet:   coverage: {detail}")
        if self.git is not None:
            g = self.git
            lines.append(
                f"riskratchet:   git: enabled={g['enabled']} "
                f"repo_present={g['repo_present']} churn_window_days={g['churn_window_days']}"
            )
        if self.filters is not None:
            f = self.filters
            lines.append(
                f"riskratchet:   filters: include={f['include']} exclude={f['exclude']} "
                f"allow={f['allow']} suppressed={f['suppressed_functions']}"
            )
        if self.analysis is not None:
            a = self.analysis
            lines.append(
                f"riskratchet:   analysis: coverage_status={a['coverage_status']} "
                f"analyzed={a['analyzed_functions']} reported={a['reported_functions']} "
                f"skipped_missing_coverage={a['skipped_missing_coverage']}"
            )
        if self.baseline is not None:
            b = self.baseline
            lines.append(
                f"riskratchet:   baseline: path={b['path']} present={b['present']} "
                f"entry_count={b['entry_count']}"
            )
        return lines


def write_debug_json(diag: Diagnostics, destination: Path | None) -> str | None:
    """Serialize `diag` to a file when `destination` is set; else return the JSON.

    Returning the string (rather than writing to stderr here) keeps all stderr
    writes funnelled through the CLI's typer helpers and out of this pure module.
    """
    payload = diag.to_json()
    if destination is None:
        return payload
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(payload + "\n", encoding="utf-8")
    return None
