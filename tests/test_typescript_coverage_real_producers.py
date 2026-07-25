"""Real-producer coverage validation gate (B2c, 0.3.0).

Closes the contract §2 pre-0.3.0 requirement: before TS coverage feeds scoring, validate the parser
against **real** output from every supported producer (c8, nyc, Jest, Vitest, Karma), whose
`DA`/`BRDA` conventions genuinely differ. The fixtures under
`tests/fixtures/typescript/real_producers/` were produced by running the actual tools (see that
dir's README for versions + commands); these tests read the committed bytes, so they are stable
across environments while still exercising true tool shapes.

Also pins the B2c FNDA decision: `FN`/`FNDA` feed a source-map-misalignment cross-check only
(`fn_declaration_lines` / `spans_cover_any_function_decl`), never the scored coverage fraction.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from riskratchet.models import FunctionSpan
from riskratchet.typescript_coverage import (
    coverage_for_ts_span,
    fn_declaration_lines,
    load_istanbul_coverage,
    load_lcov_coverage,
    spans_cover_any_function_decl,
)

REAL = Path(__file__).parent / "fixtures" / "typescript" / "real_producers"

# sample.js (c8/nyc/Jest/Vitest): classify spans 5-12, greet 14-16.
SAMPLE_CLASSIFY = FunctionSpan(start_line=5, end_line=12)
SAMPLE_GREET = FunctionSpan(start_line=14, end_line=16)
# bsample.js (Karma): classify spans 1-8, greet 10-12.
BSAMPLE_CLASSIFY = FunctionSpan(start_line=1, end_line=8)
BSAMPLE_GREET = FunctionSpan(start_line=10, end_line=12)

# The Istanbul family (nyc/Jest/Vitest) is byte-identical over sample.js: statement-line DA
# (classify = 2/5 lines) and two-arm BRDA (classify = 1/4 arms).
ISTANBUL_FAMILY = ("nyc", "jest", "vitest")


def _lcov(tool: str, source: str):  # type: ignore[no-untyped-def]
    cov = load_lcov_coverage(REAL / tool / "lcov.info")
    return cov.lookup(source)


@pytest.mark.parametrize("tool", ISTANBUL_FAMILY)
def test_istanbul_family_lcov_parses_to_expected_stats(tool: str) -> None:
    fc = _lcov(tool, "sample.js")
    assert fc is not None
    classify = coverage_for_ts_span(fc, SAMPLE_CLASSIFY)
    assert classify.line_coverage == pytest.approx(0.40)  # 2 of {6,7,8,9,11}
    assert classify.branch_coverage == pytest.approx(0.25)  # 1 of 4 arms
    assert classify.missing_branch_arms == ((6, 1), (8, 0), (8, 1))
    assert classify.missing_branches == ()  # never the Python arc field
    greet = coverage_for_ts_span(fc, SAMPLE_GREET)
    assert greet.line_coverage == 1.0
    assert greet.branch_coverage is None  # no branch in greet


def test_c8_raw_v8_lcov_parses_and_diverges_from_istanbul() -> None:
    fc = _lcov("c8", "sample.js")
    assert fc is not None
    classify = coverage_for_ts_span(fc, SAMPLE_CLASSIFY)
    # c8's raw-V8 shape: whole-file DA (classify = 5/8 lines) and single-arm BRDA (1/2 branches).
    assert classify.line_coverage == pytest.approx(0.625)
    assert classify.branch_coverage == pytest.approx(0.5)
    # Empirical non-interchangeability on real output (contract §2): c8 != the Istanbul family.
    istanbul = coverage_for_ts_span(_lcov("nyc", "sample.js"), SAMPLE_CLASSIFY)
    assert classify.line_coverage != istanbul.line_coverage
    assert classify.branch_coverage != istanbul.branch_coverage


def test_karma_headless_lcov_parses() -> None:
    fc = _lcov("karma", "bsample.js")
    assert fc is not None
    classify = coverage_for_ts_span(fc, BSAMPLE_CLASSIFY)
    assert classify.line_coverage == pytest.approx(0.40)
    assert classify.branch_coverage == pytest.approx(0.25)
    assert coverage_for_ts_span(fc, BSAMPLE_GREET).line_coverage == 1.0


@pytest.mark.parametrize("tool", ("c8", "nyc"))
def test_real_istanbul_json_parses(tool: str) -> None:
    # The two distinct real Istanbul-JSON shapes (c8 = V8-derived, nyc = istanbul).
    cov = load_istanbul_coverage(REAL / tool / "coverage-final.json")
    fc = cov.lookup("sample.js")
    assert fc is not None
    classify = coverage_for_ts_span(fc, SAMPLE_CLASSIFY)
    assert 0.0 < classify.line_coverage < 1.0  # partially covered
    assert classify.branch_coverage is not None and classify.branch_coverage < 1.0
    assert coverage_for_ts_span(fc, SAMPLE_GREET).line_coverage == 1.0


@pytest.mark.parametrize("tool", ("c8", "nyc", "jest", "vitest"))
def test_fn_cross_check_reads_declaration_lines(tool: str) -> None:
    # B2c decision: FN/FNDA are a misalignment cross-check. Every producer emits FN for
    # classify (line 5) and greet (line 14) over sample.js.
    fc = _lcov(tool, "sample.js")
    assert fc is not None
    assert fn_declaration_lines(fc) == [5, 14]
    # Aligned: the real discovered spans contain the FN declaration lines.
    assert spans_cover_any_function_decl(fc, [SAMPLE_CLASSIFY, SAMPLE_GREET]) is True
    # Misaligned: spans that contain none of the FN lines (as if numbers described compiled JS).
    assert spans_cover_any_function_decl(fc, [FunctionSpan(100, 110)]) is False


def test_fn_cross_check_is_neutral_without_fn_records() -> None:
    # A synthetic record with no FN records must not be flagged as misaligned.
    from riskratchet.typescript_coverage import _lcov_from_text

    cov = _lcov_from_text("SF:x.ts\nDA:1,1\nend_of_record\n", REAL / "synthetic")
    fc = cov.lookup("x.ts")
    assert fc is not None
    assert fn_declaration_lines(fc) == []
    assert spans_cover_any_function_decl(fc, [FunctionSpan(50, 60)]) is True
