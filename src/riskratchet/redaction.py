"""Privacy-aware output redaction (P12, 0.2.9).

Redaction is an **output transform**: it rewrites the path / qualname strings
that appear in rendered output AFTER `analyze` / `compare` / `diff` /
`match_rename` have run on the original identifiers. Because baseline rename
matching happens upstream on un-redacted values, the ratchet decision (which
functions regress, the exit code) is invariant under redaction — only the
displayed strings change.

Redaction is NEVER applied to the persisted baseline file: that file is the
source of truth for future matching, so the `baseline` command does not accept
redaction flags.

Three subtleties this module handles:

- `FunctionId` appears in many nested places (a regression's `current`, a diff
  entry's `previous_id` / `previous_targets`, etc.); every one is rewritten.
- `reason` strings embed `previous_id.as_target()` for matched renames, so a
  structural id rewrite alone would leak the original target through the
  free-text reason. We scrub `reason` using a map of the ids reachable from the
  same entry.
- Diagnostics (`--verbose` / `--debug-json` / the always-on banner) carry raw
  paths too; `redact_diagnostics` / `redact_path_string` hash those so a
  `--private-comment` run cannot leak through the diagnostics surface.

Determinism: hashes are deterministic for a given (value, salt). The default
salt is derived from the commit (see `resolve_salt`), so within one CI run the
same path hashes consistently (scan/check/diff outputs correlate) while hashes
are intentionally unlinkable across commits and repositories. An explicit salt
overrides this.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from riskratchet.models import (
    BaselineEntry,
    DiffEntry,
    DiffReport,
    FunctionId,
    FunctionRisk,
    Regression,
    RiskReport,
)

if TYPE_CHECKING:
    from riskratchet.diagnostics import Diagnostics

REDACT_SALT_ENV = "RISKRATCHET_REDACT_SALT"
_HASH_LEN = 12


@dataclass(frozen=True, slots=True)
class RedactionConfig:
    """Resolved redaction settings for a single command invocation."""

    redact_paths: bool = False
    redact_qualnames: bool = False
    suppress_links: bool = False
    salt: str | None = None

    @property
    def active(self) -> bool:
        """True when any identifier rewriting is requested."""
        return self.redact_paths or self.redact_qualnames

    @property
    def drop_links(self) -> bool:
        """Whether source links must be suppressed.

        A hashed path cannot form a valid blob URL, so any path redaction forces
        links off; `--private-comment` sets `suppress_links` directly.
        """
        return self.suppress_links or self.redact_paths


def _hash(value: str, salt: str | None) -> str:
    payload = ((salt or "") + value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:_HASH_LEN]


@dataclass(frozen=True, slots=True)
class SaltResolution:
    """Resolved salt plus where it came from, so the CLI can warn on `none`."""

    salt: str | None
    source: str  # "explicit" | "env" | "config" | "auto" | "none"


def resolve_salt(
    cli_salt: str | None,
    cfg_salt: object,
    *,
    auto: Callable[[], str | None] | None = None,
) -> SaltResolution:
    """Resolve the redaction salt and record its source.

    Precedence: `--redact-salt` flag, then `RISKRATCHET_REDACT_SALT`, then
    `[tool.riskratchet] redact_salt`, then an injected `auto` derivation (the
    CLI passes one that reads `GITHUB_REPOSITORY`/`GITHUB_SHA` or falls back to
    `git rev-parse HEAD`). When nothing yields a salt the source is `none` and
    the caller warns that unsalted hashes are guessable.

    `auto` is injected (not called here) so this module stays free of git /
    subprocess concerns and is trivially unit-testable.
    """
    if cli_salt is not None:
        return SaltResolution(cli_salt, "explicit")
    env_salt = os.environ.get(REDACT_SALT_ENV)
    if env_salt:
        return SaltResolution(env_salt, "env")
    if isinstance(cfg_salt, str) and cfg_salt:
        return SaltResolution(cfg_salt, "config")
    if auto is not None:
        derived = auto()
        if derived:
            return SaltResolution(derived, "auto")
    return SaltResolution(None, "none")


def redact_path_string(path: str, cfg: RedactionConfig) -> str:
    """Hash a bare path string when path redaction is active; else return it.

    Used for the diagnostics surfaces (banner / `--verbose` / `--debug-json`),
    which carry paths outside the `FunctionId` machinery.
    """
    if not cfg.redact_paths or not path:
        return path
    return _hash(path, cfg.salt)


def redact_function_id(fid: FunctionId, cfg: RedactionConfig) -> FunctionId:
    path = _hash(fid.path, cfg.salt) if cfg.redact_paths else fid.path
    qualname = _hash(fid.qualname, cfg.salt) if cfg.redact_qualnames else fid.qualname
    if path == fid.path and qualname == fid.qualname:
        return fid
    return FunctionId(path=path, qualname=qualname)


def redact_function_risk(fn: FunctionRisk, cfg: RedactionConfig) -> FunctionRisk:
    # Only the FunctionId is redacted: no renderer emits `file_stats.path`
    # (they use `file_stats.total_lines`), so hashing it would be dead work.
    new_id = redact_function_id(fn.id, cfg)
    if new_id is fn.id:
        return fn
    return replace(fn, id=new_id)


def _redact_baseline_entry(entry: BaselineEntry, cfg: RedactionConfig) -> BaselineEntry:
    new_id = redact_function_id(entry.id, cfg)
    if new_id is entry.id:
        return entry
    return replace(entry, id=new_id)


def _target_map(ids: list[FunctionId], cfg: RedactionConfig) -> dict[str, str]:
    """Map each id's original `as_target()` to its redacted form."""
    mapping: dict[str, str] = {}
    for fid in ids:
        original = fid.as_target()
        redacted = redact_function_id(fid, cfg).as_target()
        if original != redacted:
            mapping[original] = redacted
    return mapping


def _scrub(text: str, mapping: dict[str, str]) -> str:
    """Replace any original `as_target()` substrings in `text` with redacted ones.

    Longest originals first so a path that is a prefix of another can't shadow
    the more specific replacement.
    """
    if not text or not mapping:
        return text
    for original in sorted(mapping, key=len, reverse=True):
        if original in text:
            text = text.replace(original, mapping[original])
    return text


def redact_report(report: RiskReport, cfg: RedactionConfig) -> RiskReport:
    if not cfg.active:
        return report
    # `report.files` is only ever surfaced as a count, so it needs no redaction.
    return replace(
        report,
        functions=tuple(redact_function_risk(fn, cfg) for fn in report.functions),
    )


def redact_function(fn: FunctionRisk, cfg: RedactionConfig) -> FunctionRisk:
    if not cfg.active:
        return fn
    return redact_function_risk(fn, cfg)


def redact_regressions(regressions: list[Regression], cfg: RedactionConfig) -> list[Regression]:
    if not cfg.active:
        return regressions
    out: list[Regression] = []
    for reg in regressions:
        ids = [reg.id] + ([reg.current.id] if reg.current is not None else [])
        mapping = _target_map(ids, cfg)
        out.append(
            replace(
                reg,
                id=redact_function_id(reg.id, cfg),
                current=redact_function_risk(reg.current, cfg) if reg.current is not None else None,
                reason=_scrub(reg.reason, mapping),
            )
        )
    return out


def redact_diff(report: DiffReport, cfg: RedactionConfig) -> DiffReport:
    if not cfg.active:
        return report
    entries: list[DiffEntry] = []
    for entry in report.entries:
        ids = [entry.id, *entry.previous_targets]
        if entry.previous_id is not None:
            ids.append(entry.previous_id)
        if entry.previous is not None:
            ids.append(entry.previous.id)
        if entry.current is not None:
            ids.append(entry.current.id)
        mapping = _target_map(ids, cfg)
        entries.append(
            replace(
                entry,
                id=redact_function_id(entry.id, cfg),
                current=redact_function_risk(entry.current, cfg) if entry.current is not None else None,
                previous=_redact_baseline_entry(entry.previous, cfg) if entry.previous is not None else None,
                previous_id=(
                    redact_function_id(entry.previous_id, cfg) if entry.previous_id is not None else None
                ),
                previous_targets=tuple(redact_function_id(fid, cfg) for fid in entry.previous_targets),
                reason=_scrub(entry.reason, mapping),
            )
        )
    return DiffReport(entries=tuple(entries))


def redact_diagnostics(diag: Diagnostics, cfg: RedactionConfig) -> Diagnostics:
    """Hash the path-like fields a `Diagnostics` carries (coverage / baseline).

    Mirrors the report transform-before-render pattern so `--verbose` and
    `--debug-json` cannot leak paths that the report itself redacts. Counts,
    flags, and the command name are untouched. No-op unless `redact_paths`.
    """
    if not cfg.redact_paths:
        return diag
    new_coverage = diag.coverage
    if diag.coverage is not None:
        new_coverage = dict(diag.coverage)
        if new_coverage.get("path"):
            new_coverage["path"] = _hash(new_coverage["path"], cfg.salt)
        if new_coverage.get("map"):
            new_coverage["map"] = {key: _hash(value, cfg.salt) for key, value in new_coverage["map"].items()}
    new_baseline = diag.baseline
    if diag.baseline is not None and diag.baseline.get("path"):
        new_baseline = dict(diag.baseline)
        new_baseline["path"] = _hash(new_baseline["path"], cfg.salt)
    return replace(diag, coverage=new_coverage, baseline=new_baseline)
