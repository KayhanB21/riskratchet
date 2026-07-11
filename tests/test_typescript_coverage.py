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
    load_istanbul_coverage_files,
    spans_cover_any_statement,
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
    # TS arms live in the TS-specific field; the Python `missing_branches` stays empty.
    assert stats.missing_branch_arms == ()
    assert stats.missing_branches == ()


def test_partial_function_reports_missing_line_and_uncovered_branch() -> None:
    stats = coverage_for_ts_span(_file_cov(), PARTIAL_SPAN)
    # measured lines 7,8,9,11,13; line 11 (the else body) never executed.
    assert stats.line_coverage == pytest.approx(0.8)
    assert stats.missing_lines == (11,)
    # if-branch: then-arm taken, else-arm not → 1 of 2 arms covered, else-arm at index 1.
    assert stats.branch_coverage == pytest.approx(0.5)
    assert stats.missing_branch_arms == ((8, 1),)
    assert stats.missing_branches == ()  # never the Python field


def test_line_coverage_measures_statement_start_lines_not_spanned_lines() -> None:
    # A multi-line statement (start 8, end 12) must contribute ONLY to its start line.
    # Lines 9 and 11 carry their own statements; lines 10 and 12 carry none. So the measured
    # set is exactly {8, 9, 11} — directly proving start-line keying, not span expansion.
    file_cov = {
        "statementMap": {
            "0": {"start": {"line": 8, "column": 2}, "end": {"line": 12, "column": 3}},  # multi-line
            "1": {"start": {"line": 9, "column": 4}, "end": {"line": 9, "column": 10}},  # executed
            "2": {"start": {"line": 11, "column": 4}, "end": {"line": 11, "column": 10}},  # missing
        },
        "s": {"0": 1, "1": 1, "2": 0},
    }
    stats = coverage_for_ts_span(file_cov, FunctionSpan(start_line=6, end_line=14))
    # measured = {8, 9, 11}; line 11 not executed. Lines 10 and 12 are NOT measured (no
    # statement starts there) even though statement 0 spans across them.
    assert stats.missing_lines == (11,)
    assert stats.line_coverage == pytest.approx(2 / 3)


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


# ---- branch logic across shapes (was only ever one `if`) -------------------------------


def _branch_file_cov(branch_map: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    # Minimal file entry with one trivial executed statement so the line side is well-defined.
    return {
        "statementMap": {"0": {"start": {"line": 2, "column": 0}, "end": {"line": 2, "column": 1}}},
        "s": {"0": 1},
        "branchMap": branch_map,
        "b": b,
    }


def test_branch_switch_three_arms_one_uncovered() -> None:
    file_cov = _branch_file_cov(
        {
            "0": {
                "type": "switch",
                "loc": {"start": {"line": 3, "column": 2}, "end": {"line": 9, "column": 3}},
                "locations": [{"start": {"line": 4}}, {"start": {"line": 6}}, {"start": {"line": 8}}],
            }
        },
        {"0": [1, 0, 2]},  # arm 1 (the middle case) never taken
    )
    stats = coverage_for_ts_span(file_cov, FunctionSpan(start_line=1, end_line=10))
    assert stats.branch_coverage == pytest.approx(2 / 3)
    assert stats.missing_branch_arms == ((3, 1),)


def test_branch_logical_and_plus_second_branch_same_span() -> None:
    file_cov = _branch_file_cov(
        {
            "0": {  # a && b on line 3
                "type": "binary-expr",
                "loc": {"start": {"line": 3, "column": 2}, "end": {"line": 3, "column": 20}},
                "locations": [{"start": {"line": 3}}, {"start": {"line": 3}}],
            },
            "1": {  # an if on line 5
                "type": "if",
                "loc": {"start": {"line": 5, "column": 2}, "end": {"line": 7, "column": 3}},
                "locations": [{"start": {"line": 5}}, {"start": {"line": 6}}],
            },
        },
        {"0": [1, 0], "1": [1, 1]},  # one operand short-circuited; the if fully covered
    )
    stats = coverage_for_ts_span(file_cov, FunctionSpan(start_line=1, end_line=10))
    # 4 arms total, 3 covered.
    assert stats.branch_coverage == pytest.approx(3 / 4)
    assert stats.missing_branch_arms == ((3, 1),)


def test_branch_outside_span_is_excluded() -> None:
    file_cov = _branch_file_cov(
        {
            "0": {
                "type": "if",
                "loc": {"start": {"line": 40, "column": 2}, "end": {"line": 42, "column": 3}},
                "locations": [{"start": {"line": 40}}, {"start": {"line": 41}}],
            }
        },
        {"0": [1, 0]},
    )
    # The branch is on line 40, well outside the span → not counted at all.
    stats = coverage_for_ts_span(file_cov, FunctionSpan(start_line=1, end_line=10))
    assert stats.branch_coverage is None
    assert stats.missing_branch_arms == ()


# ---- R1 source-map misalignment detection ---------------------------------------------


def test_spans_cover_any_statement_true_when_aligned() -> None:
    file_cov = _file_cov()
    assert spans_cover_any_statement(file_cov, [COVERED_SPAN, PARTIAL_SPAN]) is True


def test_spans_cover_any_statement_false_when_misaligned() -> None:
    # Statements live on lines 2-13, but the "discovered" spans are far away (as if the
    # report measured compiled JS at different line numbers).
    file_cov = _file_cov()
    shifted = [FunctionSpan(start_line=200, end_line=210)]
    assert spans_cover_any_statement(file_cov, shifted) is False


def test_spans_cover_any_statement_true_when_no_statements() -> None:
    # No statements at all → nothing to misalign; not flagged.
    assert spans_cover_any_statement({"statementMap": {}, "s": {}}, [COVERED_SPAN]) is True


# ---- R8 repeatable / merged reports ---------------------------------------------------


def test_load_multiple_reports_merges_disjoint_packages(tmp_path: Path) -> None:
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps({"pkg-a/x.ts": {"statementMap": {}, "s": {}}}), encoding="utf-8")
    b.write_text(json.dumps({"pkg-b/y.ts": {"statementMap": {}, "s": {}}}), encoding="utf-8")
    data = load_istanbul_coverage_files([a, b])
    assert data.lookup("x.ts") is not None
    assert data.lookup("y.ts") is not None


def test_load_multiple_reports_skips_bad_shard_with_callback(tmp_path: Path) -> None:
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"pkg/x.ts": {"statementMap": {}, "s": {}}}), encoding="utf-8")
    errors: list[str] = []
    data = load_istanbul_coverage_files(
        [tmp_path / "absent.json", good],
        on_error=lambda path, msg: errors.append(str(path)),
    )
    assert data.lookup("x.ts") is not None  # good shard still loaded
    assert any("absent.json" in e for e in errors)


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


def test_scan_ts_coverage_annotates_stderr_listing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # In the human (table) format the coverage lands on the stderr TS listing, incl. missing lines.
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    from typer.testing import CliRunner

    from riskratchet.cli import app

    runner = CliRunner()
    monkeypatch.chdir(_isolated_app(tmp_path))
    result = runner.invoke(
        app,
        ["scan", ".", "--experimental-typescript", "--ts-coverage", "coverage-final.json", "--no-auto-cov"],
    )
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert "cov 100% line" in result.stderr  # covered()
    assert "cov 80% line / 50% branch" in result.stderr  # partial()
    assert "miss-lines 11" in result.stderr


def test_scan_ts_coverage_embedded_in_json_and_keeps_stdout_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Since slice 5, `--json` carries the coverage in the embedded `typescript` section (stdout),
    # and the human listing is suppressed so it never pollutes the machine-readable contract.
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
    payload = json.loads(result.stdout)  # raises if stdout was polluted
    by_name = {fn["qualname"]: fn for fn in payload["typescript"]}
    assert by_name["covered"]["line_coverage"] == 1.0
    assert by_name["partial"]["line_coverage"] == 0.8
    assert by_name["partial"]["branch_coverage"] == 0.5
    assert "cov 100% line" not in result.stderr  # human listing suppressed in --json


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
    assert "--ts-coverage / --ts-entry have no effect without --experimental-typescript" in result.stderr


def _run_ts_scan(app_dir: Path, monkeypatch: pytest.MonkeyPatch, *extra: str) -> Any:
    from typer.testing import CliRunner

    from riskratchet.cli import app

    monkeypatch.chdir(app_dir)
    return CliRunner().invoke(app, ["scan", ".", "--experimental-typescript", "--no-auto-cov", *extra])


def test_scan_warns_and_omits_coverage_when_line_numbers_misaligned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R1: a report whose line numbers describe compiled JS (no statement lands in any
    discovered span) must warn and show NO coverage, not confidently-wrong numbers."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "sample.ts").write_text((APP / "sample.ts").read_text(encoding="utf-8"), encoding="utf-8")
    # Same shape as the real fixture but every statement/branch line shoved to 200+, as if the
    # coverage was collected on compiled output.
    raw = json.loads((APP / "coverage-final.json").read_text(encoding="utf-8"))
    entry = raw["src/app/sample.ts"]
    for sid in entry["statementMap"]:
        for end in ("start", "end"):
            entry["statementMap"][sid][end]["line"] += 200
    for bid in entry["branchMap"]:
        entry["branchMap"][bid]["loc"]["start"]["line"] += 200
    (app_dir / "shifted.json").write_text(json.dumps(raw), encoding="utf-8")

    result = _run_ts_scan(app_dir, monkeypatch, "--ts-coverage", "shifted.json")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert "don't intersect any discovered function" in result.stderr
    assert "% line" not in result.stderr  # no coverage annotation was emitted at all


def test_scan_hints_when_file_has_no_coverage_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """R4: a discovered file absent from the report is reported explicitly, not silently."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "other.ts").write_text("export function f(): number {\n  return 1;\n}\n", encoding="utf-8")
    # A report that measures some unrelated file, so other.ts has no entry.
    (app_dir / "cov.json").write_text(
        json.dumps({"pkg/unrelated.ts": {"statementMap": {}, "s": {}}}), encoding="utf-8"
    )
    result = _run_ts_scan(app_dir, monkeypatch, "--ts-coverage", "cov.json")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert "1 file(s) had no coverage entry" in result.stderr


def test_scan_accepts_multiple_ts_coverage_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """R8: two reports (one per package) merge; the function gets coverage from whichever
    report measured its file."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "sample.ts").write_text((APP / "sample.ts").read_text(encoding="utf-8"), encoding="utf-8")
    # One report measures sample.ts; an unrelated second report is also passed.
    (app_dir / "a.json").write_text(
        (APP / "coverage-final.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (app_dir / "b.json").write_text(
        json.dumps({"pkg-b/other.ts": {"statementMap": {}, "s": {}}}), encoding="utf-8"
    )
    result = _run_ts_scan(app_dir, monkeypatch, "--ts-coverage", "a.json", "--ts-coverage", "b.json")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert "cov 100% line" in result.stderr  # sample.ts::covered resolved via report a
