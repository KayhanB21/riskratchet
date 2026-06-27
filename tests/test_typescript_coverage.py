"""Experimental TypeScript coverage mapping (P20 slice 3, since 0.2.13).

Maps Istanbul/nyc `coverage-final.json` onto discovered function spans. The mapping itself
is pure JSON (no tree-sitter), so the bulk of this module runs in a default Python-only env;
only the end-to-end discovery+enrichment and CLI tests need the `typescript` extra and skip
when it is absent.

The fixture `tests/fixtures/typescript/app/` is hand-authored (no nyc run) so the suite stays
hermetic: `sample.ts` has two functions — `covered` (lines 1-4, fully covered) and `partial`
(lines 6-14, one missing line + an uncovered else-arm) — and `coverage-final.json` is the
minimal Istanbul payload that yields exactly that.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from riskratchet.coverage import MissingCoveragePolicy
from riskratchet.models import FunctionSpan
from riskratchet.typescript_coverage import (
    coverage_for_ts_span,
    empty_istanbul_coverage,
    load_istanbul_coverage,
)

APP = Path(__file__).parent / "fixtures" / "typescript" / "app"
COVERAGE_FILE = APP / "coverage-final.json"

COVERED_SPAN = FunctionSpan(start_line=1, end_line=4)
PARTIAL_SPAN = FunctionSpan(start_line=6, end_line=14)


def _file_cov() -> dict[str, Any]:
    data = load_istanbul_coverage(COVERAGE_FILE)
    file_cov = data.lookup("sample.ts")
    assert file_cov is not None
    return file_cov


# ---- pure mapping (no tree-sitter) ----------------------------------------------------


def test_fully_covered_function_is_100_percent_no_branch() -> None:
    stats = coverage_for_ts_span(_file_cov(), COVERED_SPAN)
    assert stats.line_coverage == 1.0
    # No branch falls inside covered()'s span → branch coverage not measured.
    assert stats.branch_coverage is None
    assert stats.missing_lines == ()
    assert stats.missing_branches == ()


def test_partial_function_reports_missing_line_and_uncovered_branch() -> None:
    stats = coverage_for_ts_span(_file_cov(), PARTIAL_SPAN)
    # measured lines 7,8,9,11,13; line 11 (the else body) never executed.
    assert stats.line_coverage == pytest.approx(0.8)
    assert stats.missing_lines == (11,)
    # if-branch: then-arm taken, else-arm not → 1 of 2 arms covered, else-arm at index 1.
    assert stats.branch_coverage == pytest.approx(0.5)
    assert stats.missing_branches == ((8, 1),)


def test_line_coverage_keys_on_statement_start_line_only() -> None:
    # statement id 3 spans lines 8-12 but only contributes to its start line (8); the
    # multi-line statement must not mark lines 9-12 as measured by itself.
    stats = coverage_for_ts_span(_file_cov(), PARTIAL_SPAN)
    # 5 distinct measured start-lines, not 8 (would be the case if we expanded spans).
    measured = len(stats.missing_lines) + round(stats.line_coverage * 5)
    assert measured == 5


def test_missing_file_pessimistic_is_uncovered() -> None:
    stats = coverage_for_ts_span(None, PARTIAL_SPAN, missing_policy=MissingCoveragePolicy.PESSIMISTIC)
    assert stats.line_coverage == 0.0
    assert stats.branch_coverage is None


@pytest.mark.parametrize("policy", [MissingCoveragePolicy.OPTIMISTIC, MissingCoveragePolicy.SKIP])
def test_missing_file_optimistic_and_skip_do_not_penalize(policy: MissingCoveragePolicy) -> None:
    stats = coverage_for_ts_span(None, PARTIAL_SPAN, missing_policy=policy)
    assert stats.line_coverage == 1.0
    assert stats.branch_coverage is None


def test_no_measurable_statements_in_span_is_fully_covered() -> None:
    # A span over only the blank line 5 has no statements → treated as fully covered.
    stats = coverage_for_ts_span(_file_cov(), FunctionSpan(start_line=5, end_line=5))
    assert stats.line_coverage == 1.0
    assert stats.branch_coverage is None


def test_empty_statement_map_is_fully_covered() -> None:
    stats = coverage_for_ts_span({"statementMap": {}, "s": {}}, PARTIAL_SPAN)
    assert stats.line_coverage == 1.0
    assert stats.branch_coverage is None


def test_lookup_matches_by_basename_and_suffix() -> None:
    data = load_istanbul_coverage(COVERAGE_FILE)
    # Exact, suffix, and basename forms all resolve to the same entry; an absent file is None.
    assert data.lookup("src/app/sample.ts") is not None
    assert data.lookup("app/sample.ts") is not None
    assert data.lookup("sample.ts") is not None
    assert data.lookup("nope.ts") is None


def test_empty_istanbul_coverage_finds_nothing() -> None:
    assert empty_istanbul_coverage().lookup("sample.ts") is None


def test_load_rejects_non_object_payload(tmp_path: Path) -> None:
    bad = tmp_path / "coverage-final.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ValueError, match="not a JSON object"):
        load_istanbul_coverage(bad)


def test_load_missing_file_raises_filenotfound(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_istanbul_coverage(tmp_path / "absent.json")


def test_fixture_payload_shape_is_istanbul() -> None:
    # Guards the hand-authored fixture against drift from the real Istanbul shape.
    raw = json.loads(COVERAGE_FILE.read_text(encoding="utf-8"))
    entry = raw["src/app/sample.ts"]
    assert set(entry) >= {"statementMap", "branchMap", "fnMap", "s", "b", "f"}
    assert entry["b"]["0"] == [1, 0]


# ---- end-to-end discovery + enrichment (needs the typescript extra) --------------------


def test_discovered_spans_align_with_istanbul_coverage() -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    from dataclasses import replace

    from riskratchet.typescript import discover_typescript

    data = load_istanbul_coverage(COVERAGE_FILE)
    functions = discover_typescript(APP / "sample.ts", root=APP)
    enriched = {
        fn.id.qualname: replace(fn, coverage=coverage_for_ts_span(data.lookup(fn.id.path), fn.span))
        for fn in functions
    }
    assert set(enriched) == {"covered", "partial"}
    covered = enriched["covered"].coverage
    partial = enriched["partial"].coverage
    assert covered is not None and partial is not None
    assert covered.line_coverage == 1.0
    assert partial.line_coverage == pytest.approx(0.8)
    assert partial.missing_lines == (11,)
    assert partial.branch_coverage == pytest.approx(0.5)


# ---- CLI wiring (needs the typescript extra) ------------------------------------------


def _isolated_app(tmp_path: Path) -> Path:
    """Copy the app fixture into a tmp dir outside the repo, so config discovery doesn't
    walk up to riskratchet's own `[tool.riskratchet]` (whose `exclude = tests/**` would eat
    the fixture). Returns the tmp app dir."""
    dest = tmp_path / "app"
    dest.mkdir()
    (dest / "sample.ts").write_text((APP / "sample.ts").read_text(encoding="utf-8"), encoding="utf-8")
    (dest / "coverage-final.json").write_text(
        (APP / "coverage-final.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return dest


def test_scan_ts_coverage_annotates_stderr_and_keeps_stdout_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    from typer.testing import CliRunner

    from riskratchet.cli import app

    runner = CliRunner()
    monkeypatch.chdir(_isolated_app(tmp_path))
    result = runner.invoke(
        app,
        [
            "scan",
            ".",
            "--experimental-typescript",
            "--ts-coverage",
            "coverage-final.json",
            "--json",
            "--no-auto-cov",
        ],
    )
    assert result.exit_code == 0, (result.stdout, result.stderr)
    # stdout stays a valid JSON payload (no .py functions here → empty), proving the
    # experimental listing never corrupts the machine-readable contract.
    json.loads(result.stdout)
    # The coverage annotations land on stderr.
    assert "cov 100% line" in result.stderr  # covered()
    assert "cov 80% line / 50% branch" in result.stderr  # partial()
    assert "miss-lines 11" in result.stderr


def test_scan_ts_coverage_without_experimental_flag_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from riskratchet.cli import app

    runner = CliRunner()
    monkeypatch.chdir(_isolated_app(tmp_path))
    result = runner.invoke(
        app,
        ["scan", ".", "--ts-coverage", "coverage-final.json", "--no-auto-cov"],
    )
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert "--ts-coverage has no effect without --experimental-typescript" in result.stderr
