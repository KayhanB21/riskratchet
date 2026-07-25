"""Baseline JSON I/O and serialization.

The baseline is the canonical "what we tolerated last time" snapshot:
build one from a fresh `RiskReport`, write it to disk, and read it back.
The comparison logic that consumes a loaded baseline lives in the
`compare` / `diff` / `regressions` family modules; this leaf only knows
how to move a `Baseline` to and from JSON.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from riskratchet.models import (
    Baseline,
    BaselineEntry,
    FunctionId,
    RiskComponents,
    RiskReport,
)

BASELINE_VERSION = "3"


def baseline_from_report(report: RiskReport) -> Baseline:
    entries: dict[FunctionId, BaselineEntry] = {}
    for fn in report.functions:
        entries[fn.id] = BaselineEntry(
            id=fn.id,
            score=round(fn.score, 4),
            components=fn.components,
            fingerprint=fn.fingerprint,
            signature=fn.signature,
            group=fn.group,
            language=fn.language,
        )
    return Baseline(version=BASELINE_VERSION, entries=entries, identity=_identity_for(entries))


def _identity_for(entries: dict[FunctionId, BaselineEntry]) -> dict[str, Any]:
    """Per-non-Python-language fingerprint provenance, written only when such an entry exists, so a
    Python-only baseline carries no `identity` block and stays byte-stable across the v2→v3 bump."""
    identity: dict[str, Any] = {}
    if any(entry.language == "typescript" for entry in entries.values()):
        from riskratchet.typescript_identity import SCHEME_VERSION, grammar_version

        identity["typescript"] = {"scheme": SCHEME_VERSION, "grammar": grammar_version()}
    return identity


def save_baseline(baseline: Baseline, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_dumps(baseline), encoding="utf-8")


def load_baseline(path: Path) -> Baseline:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read baseline {path}: {exc}") from exc

    version = str(raw.get("version", BASELINE_VERSION))
    entries: dict[FunctionId, BaselineEntry] = {}
    for raw_entry in raw.get("entries", []):
        entry = _entry_from_dict(raw_entry)
        if entry is not None:
            entries[entry.id] = entry
    raw_identity = raw.get("identity")
    identity = raw_identity if isinstance(raw_identity, dict) else {}
    return Baseline(version=version, entries=entries, identity=identity)


def runtime_typescript_identity() -> dict[str, Any]:
    """The TS fingerprint scheme + grammar version of the *current* runtime (what fresh TS
    fingerprints are being produced with)."""
    from riskratchet.typescript_identity import SCHEME_VERSION, grammar_version

    return {"scheme": SCHEME_VERSION, "grammar": grammar_version()}


def typescript_identity_stale(baseline: Baseline) -> bool:
    """True when the baseline recorded a TypeScript identity that differs from the runtime's.

    A grammar or scheme bump silently changes every TS fingerprint, so the persisted fingerprints
    can no longer be trusted for rename matching — a moved function would look new, and a coincidental
    hash collision could look like a spurious rename. Detecting the mismatch lets the caller fall back
    to id-only matching for TS. Returns False when the extra is absent (no TS analysis is happening).
    """
    import importlib.metadata

    persisted = baseline.identity.get("typescript")
    if not persisted:
        return False
    try:
        return persisted != runtime_typescript_identity()
    except importlib.metadata.PackageNotFoundError:
        return False


def suppress_stale_typescript_renames(baseline: Baseline, report: RiskReport) -> tuple[Baseline, RiskReport]:
    """Clear TS fingerprints on both the baseline and the report so a stale-grammar baseline matches
    TypeScript functions by **id only** — never by a fingerprint made under a different grammar.
    Python entries are untouched, so mixed-language baselines keep full Python rename matching."""
    from dataclasses import replace

    new_entries = {
        fid: (replace(entry, fingerprint=None, signature=None) if entry.language == "typescript" else entry)
        for fid, entry in baseline.entries.items()
    }
    new_functions = tuple(
        replace(fn, fingerprint=None, signature=None) if fn.language == "typescript" else fn
        for fn in report.functions
    )
    return replace(baseline, entries=new_entries), replace(report, functions=new_functions)


def _dumps(baseline: Baseline) -> str:
    payload: dict[str, Any] = {"version": baseline.version}
    # `identity` sits between version and entries, present only for a baseline that carries a
    # non-Python entry (a Python-only baseline omits it, staying byte-stable across v2→v3).
    if baseline.identity:
        payload["identity"] = baseline.identity
    payload["entries"] = [
        _entry_to_dict(entry)
        for entry in sorted(
            baseline.entries.values(),
            key=lambda e: (e.id.path, e.id.qualname),
        )
    ]
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"


def _entry_to_dict(entry: BaselineEntry) -> dict[str, Any]:
    c = entry.components
    payload: dict[str, Any] = {
        "path": entry.id.path,
        "qualname": entry.id.qualname,
        "score": round(entry.score, 4),
        "components": {
            "coverage_gap": round(c.coverage_gap, 4),
            "structural_complexity": round(c.structural_complexity, 4),
            "branch_gap": round(c.branch_gap, 4),
            "churn": round(c.churn, 4),
            "public_surface": round(c.public_surface, 4),
            "sprawl": round(c.sprawl, 4),
        },
    }
    if entry.fingerprint is not None:
        payload["fingerprint"] = entry.fingerprint
    if entry.signature is not None:
        payload["signature"] = entry.signature
    if entry.group is not None:
        payload["group"] = entry.group
    # Omit-when-python: a Python entry writes no `language`, so v2 Python baselines are byte-stable.
    if entry.language != "python":
        payload["language"] = entry.language
    return payload


def _entry_from_dict(raw: Any) -> BaselineEntry | None:
    if not isinstance(raw, dict):
        return None
    path = raw.get("path")
    qualname = raw.get("qualname")
    score = raw.get("score")
    components_raw = raw.get("components")
    fingerprint = raw.get("fingerprint")
    signature = raw.get("signature")
    group = raw.get("group")
    language = raw.get("language")  # absent on v2 / Python entries → "python"
    if not (
        isinstance(path, str)
        and isinstance(qualname, str)
        and isinstance(score, (int, float))
        and isinstance(components_raw, dict)
    ):
        return None
    components = RiskComponents(
        coverage_gap=float(components_raw.get("coverage_gap", 0.0)),
        structural_complexity=float(components_raw.get("structural_complexity", 0.0)),
        branch_gap=float(components_raw.get("branch_gap", 0.0)),
        churn=float(components_raw.get("churn", 0.0)),
        public_surface=float(components_raw.get("public_surface", 0.0)),
        sprawl=float(components_raw.get("sprawl", 0.0)),
    )
    return BaselineEntry(
        id=FunctionId(path=path, qualname=qualname),
        score=float(score),
        components=components,
        fingerprint=fingerprint if isinstance(fingerprint, str) else None,
        signature=signature if isinstance(signature, str) else None,
        group=group if isinstance(group, str) else None,
        language=language if isinstance(language, str) else "python",
    )
