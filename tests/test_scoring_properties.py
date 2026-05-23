"""Hypothesis-based property tests for the scoring invariants.

Targets the contract the rest of the package relies on:
- the total risk score stays in `[0, 100]`
- increasing coverage never increases the coverage-gap score
- increasing branch coverage never increases the branch-gap score
- increasing cyclomatic complexity never decreases the structural score
- `compare()` is invariant under reordering of its inputs
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from riskratchet.baseline import compare
from riskratchet.models import (
    Baseline,
    BaselineEntry,
    ChurnStats,
    ComplexityStats,
    CoverageStats,
    FileStats,
    FunctionId,
    FunctionRisk,
    FunctionSpan,
    RiskComponents,
    RiskReport,
)
from riskratchet.scoring import (
    branch_gap_score,
    coverage_gap_score,
    structural_complexity_score,
    total_risk,
)

_components = st.builds(
    RiskComponents,
    coverage_gap=st.floats(min_value=0.0, max_value=100.0),
    structural_complexity=st.floats(min_value=0.0, max_value=100.0),
    branch_gap=st.floats(min_value=0.0, max_value=100.0),
    churn=st.floats(min_value=0.0, max_value=100.0),
    public_surface=st.floats(min_value=0.0, max_value=100.0),
    sprawl=st.floats(min_value=0.0, max_value=100.0),
)


@given(_components)
def test_total_risk_stays_in_zero_to_one_hundred(components: RiskComponents) -> None:
    score = total_risk(components)
    assert 0.0 <= score <= 100.0


@given(
    st.floats(min_value=0.0, max_value=1.0),
    st.floats(min_value=0.0, max_value=1.0),
)
def test_higher_coverage_never_raises_coverage_gap(a: float, b: float) -> None:
    lo, hi = sorted((a, b))
    score_lo = coverage_gap_score(CoverageStats(line_coverage=lo, branch_coverage=None))
    score_hi = coverage_gap_score(CoverageStats(line_coverage=hi, branch_coverage=None))
    assert score_hi <= score_lo + 1e-9


@given(
    st.floats(min_value=0.0, max_value=1.0),
    st.floats(min_value=0.0, max_value=1.0),
)
def test_higher_branch_coverage_never_raises_branch_gap(a: float, b: float) -> None:
    lo, hi = sorted((a, b))
    score_lo = branch_gap_score(CoverageStats(line_coverage=0.0, branch_coverage=lo))
    score_hi = branch_gap_score(CoverageStats(line_coverage=0.0, branch_coverage=hi))
    assert score_hi <= score_lo + 1e-9


@given(st.integers(min_value=1, max_value=200), st.integers(min_value=1, max_value=200))
def test_higher_complexity_never_lowers_structural_score(a: int, b: int) -> None:
    lo, hi = sorted((a, b))
    score_lo = structural_complexity_score(ComplexityStats(cyclomatic=lo))
    score_hi = structural_complexity_score(ComplexityStats(cyclomatic=hi))
    assert score_hi >= score_lo - 1e-9


def _zero_components() -> RiskComponents:
    return RiskComponents(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def _fn(qualname: str, score: float) -> FunctionRisk:
    path = "m.py"
    return FunctionRisk(
        id=FunctionId(path=path, qualname=qualname),
        span=FunctionSpan(start_line=1, end_line=5),
        is_public=True,
        complexity=ComplexityStats(cyclomatic=1),
        coverage=CoverageStats(line_coverage=1.0, branch_coverage=None),
        churn=ChurnStats(commits=0),
        file_stats=FileStats(path=path, total_lines=10, function_count=1),
        components=_zero_components(),
        score=score,
        crap=0.0,
    )


@given(st.lists(st.tuples(st.text(min_size=1, max_size=8, alphabet=st.characters(min_codepoint=97, max_codepoint=122)), st.floats(min_value=0.0, max_value=100.0)), min_size=1, max_size=10, unique_by=lambda pair: pair[0]))
def test_compare_is_order_independent(pairs: list[tuple[str, float]]) -> None:
    forward = tuple(_fn(name, score) for name, score in pairs)
    reverse = tuple(reversed(forward))
    report_forward = RiskReport(functions=forward, files=())
    report_reverse = RiskReport(functions=reverse, files=())
    old = Baseline(version="1", entries={})
    out_forward = compare(report_forward, old, fail_new_above=0.0, fail_regression_above=5.0)
    out_reverse = compare(report_reverse, old, fail_new_above=0.0, fail_regression_above=5.0)
    keys_forward = [(r.id.path, r.id.qualname) for r in out_forward]
    keys_reverse = [(r.id.path, r.id.qualname) for r in out_reverse]
    assert keys_forward == keys_reverse


@given(
    st.lists(
        st.tuples(
            st.text(min_size=1, max_size=8, alphabet=st.characters(min_codepoint=97, max_codepoint=122)),
            st.floats(min_value=0.0, max_value=100.0),
            st.floats(min_value=0.0, max_value=100.0),
        ),
        min_size=1,
        max_size=10,
        unique_by=lambda triple: triple[0],
    )
)
def test_compare_against_baseline_is_order_independent(
    triples: list[tuple[str, float, float]],
) -> None:
    functions = tuple(_fn(name, new) for name, _old, new in triples)
    entries = {
        FunctionId("m.py", name): BaselineEntry(
            id=FunctionId("m.py", name),
            score=old,
            components=_zero_components(),
        )
        for name, old, _new in triples
    }
    forward = RiskReport(functions=functions, files=())
    reverse = RiskReport(functions=tuple(reversed(functions)), files=())
    base = Baseline(version="1", entries=entries)
    out_forward = compare(forward, base, fail_new_above=100.0, fail_regression_above=5.0)
    out_reverse = compare(reverse, base, fail_new_above=100.0, fail_regression_above=5.0)
    assert [(r.id.qualname, r.current_score) for r in out_forward] == [
        (r.id.qualname, r.current_score) for r in out_reverse
    ]
