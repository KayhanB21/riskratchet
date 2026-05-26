"""Tests for the rename-aware baseline matcher."""

from __future__ import annotations

import ast
import textwrap

import pytest

from riskratchet.matching import (
    AMBIGUITY_BAND,
    MATCH_THRESHOLD,
    match_rename,
    signature_fingerprint,
)
from riskratchet.models import (
    BaselineEntry,
    ChurnStats,
    ComplexityStats,
    CoverageStats,
    FileStats,
    FunctionId,
    FunctionRisk,
    FunctionSpan,
    RiskComponents,
)


def _components(score: float = 50.0) -> RiskComponents:
    return RiskComponents(
        coverage_gap=score,
        structural_complexity=score,
        branch_gap=score,
        churn=score,
        public_surface=score,
        sprawl=score,
    )


def _fn(
    path: str,
    qualname: str,
    *,
    score: float = 50.0,
    component_score: float | None = None,
    fingerprint: str | None = None,
    signature: str | None = None,
) -> FunctionRisk:
    file_stats = FileStats(path=path, total_lines=100, function_count=1)
    return FunctionRisk(
        id=FunctionId(path=path, qualname=qualname),
        span=FunctionSpan(start_line=1, end_line=10),
        is_public=True,
        complexity=ComplexityStats(cyclomatic=5),
        coverage=CoverageStats(line_coverage=0.5, branch_coverage=0.5),
        churn=ChurnStats(commits=0),
        file_stats=file_stats,
        components=_components(score if component_score is None else component_score),
        score=score,
        crap=10.0,
        fingerprint=fingerprint,
        signature=signature,
    )


def _entry(
    path: str,
    qualname: str,
    *,
    score: float = 50.0,
    fingerprint: str | None = None,
    signature: str | None = None,
    component_score: float | None = None,
) -> BaselineEntry:
    return BaselineEntry(
        id=FunctionId(path=path, qualname=qualname),
        score=score,
        components=_components(score if component_score is None else component_score),
        fingerprint=fingerprint,
        signature=signature,
    )


def test_match_rename_returns_unique_best_candidate() -> None:
    """Body fingerprint match + path match easily clears the threshold."""
    fn = _fn("a.py", "new_name", score=50.0, fingerprint="body-1")
    candidates = [
        _entry("a.py", "old_name", score=50.0, fingerprint="body-1"),
        _entry("b.py", "other", score=10.0, fingerprint="other-body"),
    ]
    result = match_rename(fn, candidates)
    assert result.previous is not None
    assert result.previous.id == FunctionId("a.py", "old_name")
    assert result.is_ambiguous is False
    assert result.confidence >= MATCH_THRESHOLD


def test_match_rename_returns_ambiguous_for_near_ties() -> None:
    """Two unrelated old entries share the body fingerprint; matcher refuses to pick."""
    fn = _fn("a.py", "renamed", score=50.0, fingerprint="dup")
    candidates = [
        _entry("a.py", "one", score=50.0, fingerprint="dup"),
        _entry("a.py", "two", score=50.0, fingerprint="dup"),
    ]
    result = match_rename(fn, candidates)
    assert result.previous is None
    assert result.is_ambiguous is True
    assert len(result.candidates) == 2
    assert {c.id.qualname for c in result.candidates} == {"one", "two"}


def test_match_rename_returns_none_when_below_threshold() -> None:
    """Only signature alone (0.20) is too weak; reject as no match."""
    fn = _fn("a.py", "fn", score=80.0, fingerprint="new-body", signature="sig-x")
    candidates = [
        _entry("b.py", "other", score=10.0, fingerprint="old-body", signature="sig-x"),
    ]
    result = match_rename(fn, candidates)
    assert result.previous is None
    assert result.is_ambiguous is False


def test_match_rename_signature_supplements_body_match() -> None:
    """Signature equality contributes to the score above a body-only match."""
    # Same body fingerprint -> 0.55 alone is below 0.65. Adding signature equality
    # bumps the score to 0.75, above the threshold.
    fn = _fn(
        "renamed.py",
        "compute",
        score=50.0,
        fingerprint="shared-body",
        signature="sig-1",
        component_score=0.0,
    )
    candidates = [
        _entry(
            "old.py",
            "helper",
            score=80.0,
            fingerprint="shared-body",
            signature="sig-1",
            component_score=0.0,
        )
    ]
    result = match_rename(fn, candidates)
    assert result.previous is not None
    assert result.previous.id == FunctionId("old.py", "helper")
    assert result.confidence >= 0.75


def test_match_rename_body_changed_renames_do_not_match_silently() -> None:
    """When the body changed enough to break the fingerprint, the matcher
    conservatively refuses — the rename is reported as NEW instead of silently
    eaten."""
    fn = _fn(
        "a.py",
        "compute",
        score=50.0,
        fingerprint="changed-body",
        signature="sig-1",
        component_score=50.0,
    )
    candidates = [
        _entry(
            "a.py",
            "helper",
            score=49.0,
            fingerprint="old-body",
            signature="sig-1",
            component_score=50.0,
        )
    ]
    result = match_rename(fn, candidates)
    assert result.previous is None
    assert result.confidence < MATCH_THRESHOLD


def test_match_rename_uses_component_vector_proximity() -> None:
    """When body, signature, and path differ but component vector + score align,
    similarity stays below threshold (deliberately conservative)."""
    fn = _fn("new.py", "f", score=50.0, fingerprint="b1", signature="s1", component_score=50.0)
    candidates = [
        _entry("old.py", "g", score=51.0, fingerprint="b2", signature="s2", component_score=50.0),
    ]
    result = match_rename(fn, candidates)
    # 0.05 (component proximity) + 0.05 (score proximity) = 0.10 < 0.65
    assert result.previous is None
    assert result.is_ambiguous is False


def test_match_rename_uses_qualname_tail() -> None:
    """Class rename (path equal, tail equal, body equal) is a strong match."""
    fn = _fn("m.py", "NewClass.method", score=50.0, fingerprint="b1")
    candidates = [_entry("m.py", "OldClass.method", score=50.0, fingerprint="b1")]
    result = match_rename(fn, candidates)
    assert result.previous is not None
    assert result.previous.id.qualname == "OldClass.method"


def test_match_rename_empty_candidates_returns_no_match() -> None:
    fn = _fn("a.py", "x", score=50.0, fingerprint="b1")
    result = match_rename(fn, [])
    assert result.previous is None
    assert result.is_ambiguous is False
    assert result.candidates == ()


def test_match_rename_threshold_constants_are_in_valid_range() -> None:
    """Documented constants must stay sane so tests above remain meaningful."""
    assert 0.0 < AMBIGUITY_BAND < MATCH_THRESHOLD < 1.0


def test_signature_fingerprint_strips_name_and_locations() -> None:
    """Two functions with the same signature but different names get the same fingerprint."""
    source_a = textwrap.dedent(
        """
        def first(x: int, y: str = 'a', *, flag: bool = True) -> int:
            return 1
        """
    )
    source_b = textwrap.dedent(
        """

        def second(x: int, y: str = 'a', *, flag: bool = True) -> int:
            return 99 + x
        """
    )
    fn_a = ast.parse(source_a).body[0]
    fn_b = ast.parse(source_b).body[0]
    assert isinstance(fn_a, ast.FunctionDef)
    assert isinstance(fn_b, ast.FunctionDef)
    assert signature_fingerprint(fn_a) == signature_fingerprint(fn_b)


def test_signature_fingerprint_includes_decorators_and_defaults() -> None:
    """Different decorator sets must produce different signature fingerprints."""
    source_a = textwrap.dedent(
        """
        @decorator_one
        def f(x: int) -> int:
            return x
        """
    )
    source_b = textwrap.dedent(
        """
        def f(x: int) -> int:
            return x
        """
    )
    source_c = textwrap.dedent(
        """
        def f(x: int = 5) -> int:
            return x
        """
    )
    fn_a = ast.parse(source_a).body[0]
    fn_b = ast.parse(source_b).body[0]
    fn_c = ast.parse(source_c).body[0]
    assert isinstance(fn_a, ast.FunctionDef)
    assert isinstance(fn_b, ast.FunctionDef)
    assert isinstance(fn_c, ast.FunctionDef)
    assert signature_fingerprint(fn_a) != signature_fingerprint(fn_b)
    assert signature_fingerprint(fn_b) != signature_fingerprint(fn_c)


def test_match_rename_prefers_path_match_over_unrelated_body_when_ambiguous_avoided() -> None:
    """When body matches multiple candidates, path equality breaks the tie."""
    fn = _fn("a.py", "new", score=50.0, fingerprint="b1")
    candidates = [
        _entry("a.py", "old", score=50.0, fingerprint="b1"),
        _entry("b.py", "old", score=50.0, fingerprint="b1"),
    ]
    result = match_rename(fn, candidates)
    # Both share body+score; only one shares path -> tie broken at 0.10 > AMBIGUITY_BAND
    assert result.previous is not None
    assert result.previous.id.path == "a.py"


def test_match_rename_returns_ambiguous_when_both_share_path_and_body() -> None:
    """Two candidates in the same path with the same body -> ambiguous."""
    fn = _fn("a.py", "z", score=50.0, fingerprint="b1")
    candidates = [
        _entry("a.py", "p", score=50.0, fingerprint="b1"),
        _entry("a.py", "q", score=50.0, fingerprint="b1"),
    ]
    result = match_rename(fn, candidates)
    assert result.is_ambiguous is True
    assert result.previous is None
    assert {c.id.qualname for c in result.candidates} == {"p", "q"}


def test_match_rename_signature_alone_is_below_threshold() -> None:
    """Pinned contract: a signature-only match never clears MATCH_THRESHOLD.

    This is by design: signature fingerprint is a *corroborating* signal
    (0.20 weight), not a load-bearing one. A body-changed function whose
    signature happens to match an old entry must be reported as NEW (or
    AMBIGUOUS_RENAME if multiple candidates tie), not silently classified
    as MOVED. Allowing signature-only matches would let a body rewrite
    hide behind a stable signature.

    See `src/riskratchet/matching.py` module docstring + AGENTS.md
    "Rename matcher: known limits".
    """
    fn = _fn(
        "x.py",
        "fn",
        score=80.0,
        fingerprint="new-body",
        signature="shared-sig",
        component_score=0.0,
    )
    candidates = [
        _entry("y.py", "other", score=10.0, fingerprint="old-body", signature="shared-sig"),
    ]
    result = match_rename(fn, candidates)
    assert result.previous is None, "signature-only matches must not become a confident rename"
    assert result.confidence < MATCH_THRESHOLD, (
        f"signature-only confidence must be below threshold; got {result.confidence}"
    )


def test_match_rename_score_proximity_alone_is_below_threshold() -> None:
    """Defensive: identical score on every other-dim mismatch must not match."""
    fn = _fn("a.py", "x", score=10.0, fingerprint="b1", signature="s1", component_score=0.0)
    candidates = [
        _entry("b.py", "y", score=11.0, fingerprint="b2", signature="s2", component_score=0.0),
    ]
    result = match_rename(fn, candidates)
    assert result.previous is None


def test_match_rename_ignores_already_matched_entries() -> None:
    """The matcher only sees the candidate pool the caller passes in."""
    fn = _fn("a.py", "new", score=50.0, fingerprint="b1")
    # Empty pool: caller already matched the entry elsewhere.
    result = match_rename(fn, [])
    assert result.previous is None


@pytest.mark.parametrize(
    "score_a, score_b",
    [(50.0, 50.0), (60.0, 80.0)],
)
def test_match_rename_handles_score_variation(score_a: float, score_b: float) -> None:
    fn = _fn("a.py", "new", score=score_a, fingerprint="b1")
    candidates = [_entry("a.py", "old", score=score_b, fingerprint="b1")]
    result = match_rename(fn, candidates)
    # Body + path + tail-not-equal share path: body(0.55) + path(0.10) = 0.65 minimum -> match.
    assert result.previous is not None
