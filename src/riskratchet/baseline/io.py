"""Baseline JSON I/O and serialization.

The baseline is the canonical "what we tolerated last time" snapshot:
build one from a fresh `RiskReport`, write it to disk, and read it back.
The comparison logic that consumes a loaded baseline lives in the
`compare` / `diff` / `regressions` family modules; this leaf only knows
how to move a `Baseline` to and from JSON.
"""

from __future__ import annotations

import json
from collections.abc import Collection
from pathlib import Path
from typing import Any

from riskratchet.models import (
    Baseline,
    BaselineEntry,
    FunctionId,
    RiskComponents,
    RiskReport,
)

# Weights are floats that survive a JSON round-trip exactly at this precision, and the
# only comparison anyone makes of them is "is this the same scoring setup?". Rounding
# before both writing and comparing keeps a 0.1 + 0.2 style representation artifact from
# being reported as a changed weight.
_WEIGHT_PRECISION = 6

BASELINE_VERSION = "3"

# Every baseline version this build can *read*. `BASELINE_VERSION` is only what it
# writes; older files stay readable so upgrading riskratchet never forces a
# re-baseline. Keep in lockstep with the `version` enum in
# `schemas/baseline.schema.json` — `test_schemas.py` asserts the two agree.
SUPPORTED_BASELINE_VERSIONS = ("1", "2", "3")


class BaselineVersionError(ValueError):
    """The baseline's format version is one this build cannot read.

    A `ValueError` subclass so every existing error boundary keeps catching it,
    but distinguishable by type for the one caller that needs to: a baseline from
    a *newer* riskratchet is fixed by upgrading, not by regenerating, and telling
    the user to regenerate would destroy the newer file for no reason.
    """


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
    return Baseline(
        version=BASELINE_VERSION,
        entries=entries,
        identity=_identity_for(entries),
        scoring=_scoring_for(report),
        declared_version=BASELINE_VERSION,
    )


def _scoring_for(report: RiskReport) -> dict[str, Any]:
    """The provenance block: what scored these numbers, on every baseline.

    Unlike `identity`, this is written for Python-only baselines too — a Python baseline
    from a different scoring model, different weights, or a run where churn could not be
    collected is just as incomparable as a TypeScript one from a different grammar, and
    until 0.3.7 nothing recorded it. Empty when the report carries no `ScoringInputs`,
    so a hand-built report writes no block rather than one claiming defaults it never used.

    Carries no path, qualname, or coverage figure — only configuration — so it is safe to
    print under `private_comment` / redaction without any filtering.
    """
    inputs = report.scoring
    if inputs is None:
        return {}
    return scoring_block(
        model=inputs.model,
        weights=inputs.weights,
        churn_window_days=inputs.churn_window_days,
        churn_available=inputs.churn_available,
        coverage=report.coverage_status,
    )


def scoring_block(
    *,
    model: int,
    weights: Any,
    churn_window_days: int,
    churn_available: bool,
    coverage: str,
) -> dict[str, Any]:
    """Build a provenance block from already-resolved parts.

    Public because `doctor` builds one from config without scoring anything, and the two must
    be byte-comparable: a different key order or a different rounding here would make `doctor`
    and `check` disagree about a baseline neither of them has any reason to doubt.
    """
    return {
        "model": model,
        "weights": {name: round(float(value), _WEIGHT_PRECISION) for name, value in sorted(weights.items())},
        "churn_window_days": churn_window_days,
        "churn_available": churn_available,
        "coverage": coverage,
    }


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


def load_baseline(path: Path, *, on_dropped: Any = None) -> Baseline:
    """Read a baseline, refusing one that cannot be trusted as a ratchet.

    Structural problems raise `ValueError` instead of degrading to an empty
    baseline. That distinction matters more here than anywhere else in the tool:
    an empty baseline means *every* gate passes, so a truncated or wrong-version
    `.riskratchet.json` used to silently switch the ratchet off and report a
    clean run. Failing loudly is the only safe direction for a gate.

    `on_dropped(count)`, if given, is called once when individual entries were
    unreadable but the file as a whole was usable — those functions are missing
    from the ratchet, which is worth saying out loud. Mirrors the `on_error`
    callback on `coverage.load_coverage_map`.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read baseline {path}: {exc}") from exc

    version, declared_version = _baseline_version(raw)
    entries: dict[FunctionId, BaselineEntry] = {}
    dropped = 0
    for raw_entry in raw["entries"]:
        entry = _entry_from_dict(raw_entry)
        if entry is None:
            dropped += 1
        else:
            entries[entry.id] = entry
    # Every entry unreadable is a *structural* problem, not a partial one: the result
    # is a zero-entry baseline, which passes every gate — the exact outcome the
    # docstring above says must fail loudly. It compounds, too, because `check` feeds
    # `len(entries)` into `_require_gateable_functions`, so one condition would switch
    # off both this guard and 0.3.4's empty-scan guard. Reachable from a hand-edited
    # file, or from passing `diff --format json` output as `--baseline` — its entries
    # carry no `components`, so every one of them drops.
    if dropped and not entries:
        raise ValueError(
            f"baseline {path} has {dropped} entr{'y' if dropped == 1 else 'ies'} and none could be read"
        )
    if dropped and on_dropped is not None:
        on_dropped(dropped)
    raw_identity = raw.get("identity")
    identity = raw_identity if isinstance(raw_identity, dict) else {}
    raw_scoring = raw.get("scoring")
    scoring = raw_scoring if isinstance(raw_scoring, dict) else {}
    return Baseline(
        version=version,
        entries=entries,
        identity=identity,
        scoring=scoring,
        declared_version=declared_version,
    )


def _baseline_version(raw: Any) -> tuple[str, str | None]:
    """Validate the baseline envelope and return `(effective version, declared version)`.

    A missing `version` is read as the current one — some early files predate the
    field — but an *unknown* one is fatal, because a future entry shape would
    parse to zero usable entries and look like a clean baseline.

    The declared spelling is returned alongside, `None` when the key was absent, because
    collapsing the two loses the one fact that identifies the oldest baselines in
    existence: a file that predates the `version` field also predates the current scoring
    model, and `scoring_model_stale` has to be able to say so. Every other caller wants
    the effective version and can ignore the second element.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"baseline root must be a JSON object, got {type(raw).__name__}")
    if not isinstance(raw.get("entries"), list):
        raise ValueError("baseline has no 'entries' array")
    declared = raw.get("version")
    version = str(declared) if declared is not None else BASELINE_VERSION
    if version not in SUPPORTED_BASELINE_VERSIONS:
        raise BaselineVersionError(_unsupported_version_message(version))
    return version, (version if declared is not None else None)


def _unsupported_version_message(version: str) -> str:
    newest = SUPPORTED_BASELINE_VERSIONS[-1]
    if version.isdigit() and int(version) > int(newest):
        return (
            f"baseline v{version} was written by a newer riskratchet; "
            f"this build reads up to v{newest}. Upgrade riskratchet"
        )
    supported = ", ".join(f"v{value}" for value in SUPPORTED_BASELINE_VERSIONS)
    return f"unrecognized baseline version {version!r}; this build reads {supported}"


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


def runtime_scoring_block(report: RiskReport) -> dict[str, Any]:
    """The provenance block this run would write — what a persisted one is compared against."""
    return _scoring_for(report)


def scoring_model_stale(baseline: Baseline, report: RiskReport) -> list[str]:
    """Reasons the baseline's numbers were not produced the way this run produces them.

    Empty when they are comparable. Each reason is one short adopter-readable clause; the
    caller decides how to present them. Never raises and never fails a gate: a mismatch
    means the *comparison* is untrustworthy, and turning that into an exit code would
    break every upgrade, which is the outcome this whole mechanism exists to avoid.

    Three cases, in order:

    * **A pre-v3 envelope** (v1, v2, or no `version` key at all) was written by 0.2.x,
      before 0.3.0 redefined `sprawl`. The file carries no provenance to compare, but the
      envelope version is itself the evidence.
    * **A recorded block** is compared field by field, so the reason names what actually
      differs — which matters, because the right response is not the same for each. New
      weights in `pyproject.toml` mean "re-baseline, that was deliberate"; churn that was
      available then and is not now means "fix CI, and do NOT re-baseline, or you will bake
      the zero in".
    * **A v3 baseline with no block** (0.3.0 through 0.3.6) stays silent. v3 implies the
      current scoring model, so there is nothing to report; its weights and churn window
      are genuinely unknowable and guessing would nag every existing user on upgrade.
      `baseline_scored_without_churn` still catches the one case that leaves evidence.
    """
    envelope = scoring_envelope_reason(baseline)
    if envelope is not None:
        return [envelope]
    current = _scoring_for(report)
    if not baseline.scoring or not current:
        return []
    return scoring_block_differences(baseline.scoring, current)


def scoring_envelope_reason(baseline: Baseline) -> str | None:
    """The pre-0.3.0 evidence carried by the envelope itself, for a file with no `scoring` block.

    A v1 or v2 baseline — or one so old it has no `version` key at all — was written before
    0.3.0 redefined `sprawl`, so its scores came from a scoring model this build no longer
    implements. Split out from `scoring_model_stale` because `doctor` reaches the same verdict
    without building a report.
    """
    if baseline.declared_version is None:
        return "the baseline predates the `version` field, so riskratchet 0.2.x wrote it"
    if baseline.version != BASELINE_VERSION:
        return (
            f"the baseline is v{baseline.version}, written by riskratchet 0.2.x before the "
            f"0.3.0 scoring model"
        )
    return None


def scoring_block_differences(
    persisted: dict[str, Any],
    current: dict[str, Any],
    *,
    ignore: Collection[str] = (),
) -> list[str]:
    """Compare two provenance blocks and name every difference, one clause each.

    Public because `doctor` compares a persisted block against what the *config* resolves to,
    without scoring anything, and must reach the same verdict `check` does with a report in
    hand — the two disagreeing about whether a baseline is comparable is the failure mode this
    whole mechanism exists to remove.

    `ignore` names fields the caller cannot resolve faithfully, so it reports nothing rather
    than something wrong. `doctor` ignores `coverage` for exactly that reason: it sees whether
    a coverage file exists *now*, while a real run may be about to generate one through
    auto-coverage, so comparing it would warn about a difference that will not exist by the
    time anything is scored. `doctor`'s own coverage row already reports that state.
    """
    reasons: list[str] = []
    if "model" not in ignore and persisted.get("model") != current.get("model"):
        reasons.append(f"scoring model {persisted.get('model')} -> {current.get('model')}")
    if "weights" not in ignore:
        reasons.extend(_weight_reasons(persisted.get("weights"), current.get("weights")))
    if "churn_window_days" not in ignore and persisted.get("churn_window_days") != current.get(
        "churn_window_days"
    ):
        reasons.append(
            f"churn window {persisted.get('churn_window_days')}d -> {current.get('churn_window_days')}d"
        )
    was_churn, now_churn = persisted.get("churn_available"), current.get("churn_available")
    if "churn_available" not in ignore and was_churn != now_churn:
        reasons.append(
            "churn was collectable when the baseline was written but not in this run, so every "
            "churn component scored 0 — fix the repository access rather than re-baselining"
            if was_churn
            else "churn could not be collected when the baseline was written but can be now, so "
            "churn components will rise against baselined zeroes"
        )
    if "coverage" not in ignore and persisted.get("coverage") != current.get("coverage"):
        reasons.append(f"coverage {persisted.get('coverage')} -> {current.get('coverage')}")
    return reasons


def _weight_reasons(persisted: Any, current: Any) -> list[str]:
    """Name the components whose weight moved, not just that the vector differs.

    Both sides are already rounded to `_WEIGHT_PRECISION` by `_scoring_for`, so this
    compares what was written, never raw floats.
    """
    if not isinstance(persisted, dict) or not isinstance(current, dict):
        return []
    changed = [
        f"{name} {persisted.get(name)} -> {current.get(name)}"
        for name in sorted(set(persisted) | set(current))
        if persisted.get(name) != current.get(name)
    ]
    return [f"weights changed ({', '.join(changed)})"] if changed else []


def baseline_scored_without_churn(baseline: Baseline, report: RiskReport) -> bool:
    """True when every baselined function has zero churn but this run measured some.

    The one silent-zero a baseline without a `scoring` block still betrays. A baseline
    written where git was unreachable — a container without `.git`, a `--no-git` run, a
    source tarball — records churn 0 for every function; comparing a real run against it
    pushes the whole churn component upward at once, which reads as a mass regression
    nobody caused. Detecting it here covers the 0.3.0-0.3.6 baselines that carry no
    provenance at all, so the disclosure is not limited to files written from 0.3.7 on.

    Deliberately narrow: it requires entries on one side and measured churn on the other,
    so a genuinely quiet repository (nothing committed in the window) says nothing, and
    neither does an empty baseline.
    """
    if not baseline.entries:
        return False
    if any(entry.components.churn for entry in baseline.entries.values()):
        return False
    return any(fn.components.churn for fn in report.functions)


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
    # Persisted on every baseline that has it, so a `load` -> `save` round-trip through the
    # public API cannot quietly strip the provenance and turn a v3 baseline that knows what
    # scored it into one that does not.
    if baseline.scoring:
        payload["scoring"] = baseline.scoring
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
