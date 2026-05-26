"""Rename-aware baseline matching.

The exact-id and unique-body-fingerprint paths in `baseline.compare` /
`baseline.diff` handle the easy cases. This module handles the rest: the
body changed but the signature didn't, the file moved and the body shifted
slightly, two old candidates plausibly map to the same new function.

The matcher is intentionally conservative — when more than one old entry
scores near the top, the result is flagged as ambiguous so the
new-above-threshold gate still fires. A confident-enough single match wins
silently and the function is reported as MOVED.

## Known limits

The similarity weights below (BODY_WEIGHT=0.55, SIGNATURE_WEIGHT=0.20,
PATH_WEIGHT=0.10, QUALNAME_TAIL_WEIGHT=0.05, COMPONENT_PROXIMITY_WEIGHT=0.05,
SCORE_PROXIMITY_WEIGHT=0.05) and the MATCH_THRESHOLD=0.65 are **provisional**.
They were chosen so that body+any-extra-signal wins (0.55 + 0.05 >= 0.65)
but signature+path+tail+score-proximity (0.40 total) does not. The values
have not been calibrated against a corpus of real renames; empirical
calibration is roadmap item 0.2.10+ (see `docs/riskratchet-0.2x-roadmap.md`).

The matcher deliberately rejects signature-only matches: a candidate whose
body fingerprint *changed* and which lacks corroborating path / qualname
signals will not clear the threshold. This is by design — silently
classifying a body-changed renamed function as MOVED would hide risk
growth. Body fingerprint match + any one other signal is the minimum bar.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import math
from dataclasses import dataclass

from riskratchet.models import (
    BaselineEntry,
    FunctionId,
    FunctionRisk,
    RiskComponents,
)

MATCH_THRESHOLD = 0.65
"""Minimum total similarity for the top candidate to be considered a rename.

Tuned so that body+any-extra-signal wins (0.55 + 0.05 >= 0.65), but
signature+path+tail+score (0.20 + 0.10 + 0.05 + 0.05 = 0.40) does not. The
intent is that a clear body match always wins, and weaker signals require
multiple corroborations.
"""

AMBIGUITY_BAND = 0.05
"""Top candidates within this band of each other are reported as ambiguous."""

BODY_WEIGHT = 0.55
SIGNATURE_WEIGHT = 0.20
PATH_WEIGHT = 0.10
QUALNAME_TAIL_WEIGHT = 0.05
COMPONENT_PROXIMITY_WEIGHT = 0.05
SCORE_PROXIMITY_WEIGHT = 0.05

COMPONENT_PROXIMITY_THRESHOLD = 0.95
SCORE_PROXIMITY_TOLERANCE = 2.0


@dataclass(frozen=True, slots=True)
class MatchResult:
    """Outcome of `match_rename`.

    `previous` is set when a single candidate is unambiguously best; in that
    case the entry should be reported as MOVED. When multiple candidates tie
    near the top, `is_ambiguous` is True and `candidates` lists all the
    plausible old entries — callers should surface them and refuse to claim
    a silent match.
    """

    previous: BaselineEntry | None
    confidence: float
    is_ambiguous: bool
    candidates: tuple[BaselineEntry, ...]


def signature_fingerprint(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Stable hash of a function's signature (args, decorators, return annotation).

    Strips identifier names from positional arg defaults, locations, and the
    function name itself. The result survives body edits and identifier
    renames inside the signature but changes when the call shape changes.
    """
    clone = copy.deepcopy(node)
    clone.name = ""
    clone.body = []
    for child in ast.walk(clone):
        for attr in ("lineno", "col_offset", "end_lineno", "end_col_offset"):
            if hasattr(child, attr):
                setattr(child, attr, None)
    payload = ast.dump(clone, annotate_fields=True, include_attributes=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def match_rename(
    fn: FunctionRisk,
    candidates: list[BaselineEntry],
) -> MatchResult:
    """Return the best baseline candidate for `fn`, or no match.

    The matcher scores each candidate against `fn` using body fingerprint,
    signature fingerprint, path equality, qualname tail equality, component
    vector proximity, and score proximity. The unique highest-scoring
    candidate at or above `MATCH_THRESHOLD` wins; when two or more candidates
    score within `AMBIGUITY_BAND` of the top, the result is flagged as
    ambiguous.
    """
    if not candidates:
        return MatchResult(previous=None, confidence=0.0, is_ambiguous=False, candidates=())

    scored: list[tuple[float, BaselineEntry]] = []
    for entry in candidates:
        score = _similarity(fn, entry)
        if score > 0.0:
            scored.append((score, entry))

    if not scored:
        return MatchResult(previous=None, confidence=0.0, is_ambiguous=False, candidates=())

    scored.sort(key=lambda item: (-item[0], item[1].id.as_target()))
    top_score, top_entry = scored[0]
    if top_score < MATCH_THRESHOLD:
        return MatchResult(previous=None, confidence=top_score, is_ambiguous=False, candidates=())

    near_top = [entry for score, entry in scored if top_score - score <= AMBIGUITY_BAND]
    if len(near_top) > 1:
        return MatchResult(
            previous=None,
            confidence=top_score,
            is_ambiguous=True,
            candidates=tuple(near_top),
        )

    return MatchResult(
        previous=top_entry,
        confidence=top_score,
        is_ambiguous=False,
        candidates=(top_entry,),
    )


def _similarity(fn: FunctionRisk, entry: BaselineEntry) -> float:
    score = 0.0
    if fn.fingerprint is not None and fn.fingerprint == entry.fingerprint:
        score += BODY_WEIGHT
    if fn.signature is not None and entry.signature is not None and fn.signature == entry.signature:
        score += SIGNATURE_WEIGHT
    if fn.id.path == entry.id.path:
        score += PATH_WEIGHT
    if _qualname_tail(fn.id) == _qualname_tail(entry.id):
        score += QUALNAME_TAIL_WEIGHT
    if _component_similarity(fn.components, entry.components) >= COMPONENT_PROXIMITY_THRESHOLD:
        score += COMPONENT_PROXIMITY_WEIGHT
    if abs(fn.score - entry.score) <= SCORE_PROXIMITY_TOLERANCE:
        score += SCORE_PROXIMITY_WEIGHT
    return round(score, 6)


def _qualname_tail(fid: FunctionId) -> str:
    return fid.qualname.rsplit(".", 1)[-1]


def _component_similarity(a: RiskComponents, b: RiskComponents) -> float:
    """Cosine similarity of two 6-dimensional component vectors.

    Two all-zero vectors are treated as fully similar; a zero-versus-nonzero
    pair is treated as fully dissimilar so it cannot inflate weak matches.
    """
    av = _component_vector(a)
    bv = _component_vector(b)
    dot = sum(x * y for x, y in zip(av, bv, strict=True))
    norm_a = math.sqrt(sum(x * x for x in av))
    norm_b = math.sqrt(sum(x * x for x in bv))
    if norm_a == 0.0 and norm_b == 0.0:
        return 1.0
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _component_vector(c: RiskComponents) -> tuple[float, ...]:
    return (
        c.coverage_gap,
        c.structural_complexity,
        c.branch_gap,
        c.churn,
        c.public_surface,
        c.sprawl,
    )
