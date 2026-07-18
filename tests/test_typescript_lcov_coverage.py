"""Experimental TypeScript LCOV coverage mapping (P20 track, since 0.2.16).

LCOV (`lcov.info`) is parsed into the *same* synthetic Istanbul-shaped per-file dict the JSON
path produces, so the mapping functions are reused unchanged. The load-bearing test is the
**equivalence property**: the hand-authored `coverage.lcov` describes exactly the same measured
lines/branches as `coverage-final.json`, so both must yield byte-identical `CoverageStats` for the
same span. Its `DA:` records deliberately cover only the Istanbul *statement-start* lines
({2,3,7,8,9,11,13}) — not the function-declaration lines — so the two backends' measured sets
coincide and the equivalence holds. The mapping is pure text (no tree-sitter); only the CLI
end-to-end tests need the `typescript` extra and skip when it is absent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from riskratchet.models import FunctionSpan
from riskratchet.typescript_coverage import (
    coverage_for_ts_span,
    load_istanbul_coverage,
    load_lcov_coverage,
    load_ts_coverage_files,
    spans_cover_any_statement,
)

APP = Path(__file__).parent / "fixtures" / "typescript" / "app"
ISTANBUL_FILE = APP / "coverage-final.json"
LCOV_FILE = APP / "coverage.lcov"

COVERED_SPAN = FunctionSpan(start_line=1, end_line=4)
PARTIAL_SPAN = FunctionSpan(start_line=6, end_line=14)


def _lcov_file_cov() -> dict[str, Any]:
    data = load_lcov_coverage(LCOV_FILE)
    file_cov = data.lookup("sample.ts")
    assert file_cov is not None
    return file_cov


# ---- the equivalence property (LCOV normalizes into the Istanbul shape) ----------------


@pytest.mark.parametrize("span", [COVERED_SPAN, PARTIAL_SPAN])
def test_lcov_normalizes_to_same_stats_as_a_matched_istanbul_fixture(span: FunctionSpan) -> None:
    # NOTE: this proves the *normalization* is faithful — the two fixtures were hand-authored to
    # describe the same measured lines/branches, so equal input must give equal CoverageStats. It
    # does NOT claim real tools agree: a real `c8 --reporter=lcov` and a real
    # `nyc --reporter=json` model branches and measured lines differently and will diverge on the
    # same source (see `test_parses_real_c8_generated_lcov`, whose numbers are legitimately not the
    # Istanbul fixture's).
    istanbul = load_istanbul_coverage(ISTANBUL_FILE).lookup("sample.ts")
    lcov = load_lcov_coverage(LCOV_FILE).lookup("sample.ts")
    assert istanbul is not None and lcov is not None
    assert coverage_for_ts_span(lcov, span) == coverage_for_ts_span(istanbul, span)


C8_REAL_FILE = Path(__file__).parent / "fixtures" / "typescript" / "c8_real" / "lcov.info"


def test_parses_real_c8_generated_lcov() -> None:
    # Unlike the hand-authored fixtures, `c8_real/lcov.info` was produced by an actual
    # `c8 --reporter=lcovonly` run (source: `c8_real/sample.js`). It exercises the real byte shape:
    # DA on *every* line (declarations, braces), FNF/FNH before FNDA, and c8's per-block branch
    # model (four single-arm BRDA blocks, not Istanbul's two-arm `if`). Values below are read off
    # the committed, frozen fixture.
    data = load_lcov_coverage(C8_REAL_FILE)
    file_cov = data.lookup("sample.js")
    assert file_cov is not None

    covered = coverage_for_ts_span(file_cov, FunctionSpan(start_line=1, end_line=4))
    assert covered.line_coverage == 1.0
    assert covered.branch_coverage == 1.0  # both block-0/1 branches on line 1 taken
    assert covered.missing_lines == ()

    partial = coverage_for_ts_span(file_cov, FunctionSpan(start_line=5, end_line=13))
    assert partial.line_coverage == pytest.approx(7 / 9)  # lines 10,11 (the else body) not run
    assert partial.missing_lines == (10, 11)
    assert partial.branch_coverage == pytest.approx(0.5)  # else block (line 9) never taken
    assert partial.missing_branch_arms == ((9, 0),)


def test_real_c8_lcov_diverges_from_the_istanbul_fixture() -> None:
    # Empirical proof that LCOV and Istanbul are NOT interchangeable on real output: c8 reports the
    # uncovered `partial` branch on line 9 (the `else`) as arm 0, while the Istanbul fixture reports
    # it on line 8 as arm 1, and the line-coverage denominators differ (7/9 vs 0.8).
    c8 = load_lcov_coverage(C8_REAL_FILE).lookup("sample.js")
    istanbul = load_istanbul_coverage(ISTANBUL_FILE).lookup("sample.ts")
    assert c8 is not None and istanbul is not None
    c8_partial = coverage_for_ts_span(c8, FunctionSpan(start_line=5, end_line=13))
    ist_partial = coverage_for_ts_span(istanbul, PARTIAL_SPAN)
    assert c8_partial.missing_branch_arms != ist_partial.missing_branch_arms
    assert c8_partial.line_coverage != ist_partial.line_coverage


def test_lcov_covered_function_is_100_percent_no_branch() -> None:
    stats = coverage_for_ts_span(_lcov_file_cov(), COVERED_SPAN)
    assert stats.line_coverage == 1.0
    assert stats.branch_coverage is None
    assert stats.missing_lines == ()
    assert stats.missing_branch_arms == ()
    assert stats.missing_branches == ()  # never the Python field


def test_lcov_partial_reports_missing_line_and_uncovered_branch() -> None:
    stats = coverage_for_ts_span(_lcov_file_cov(), PARTIAL_SPAN)
    # DA lines 7,8,9,11,13 measured; line 11 (else body) has DA:11,0 → not executed.
    assert stats.line_coverage == pytest.approx(0.8)
    assert stats.missing_lines == (11,)
    # BRDA:8,0,0,1 / BRDA:8,0,1,0 → 1 of 2 arms covered, else-arm at index 1.
    assert stats.branch_coverage == pytest.approx(0.5)
    assert stats.missing_branch_arms == ((8, 1),)
    assert stats.missing_branches == ()


def test_lcov_matches_istanbul_on_switch_and_multiple_branches(tmp_path: Path) -> None:
    # A richer structure than the fixture's single `if`: a 3-arm switch + a 2-arm branch, with a
    # missing line and a missing switch arm. Built as an Istanbul dict and the equivalent LCOV text,
    # asserting identical CoverageStats — hardening the core normalization guarantee.
    istanbul = {
        "statementMap": {
            "0": {"start": {"line": 2}},
            "1": {"start": {"line": 3}},
            "2": {"start": {"line": 4}},
            "3": {"start": {"line": 6}},
            "4": {"start": {"line": 7}},
        },
        "s": {"0": 1, "1": 0, "2": 1, "3": 1, "4": 1},  # line 3 never executed
        "branchMap": {
            "0": {"loc": {"start": {"line": 4}}},  # switch, 3 arms
            "1": {"loc": {"start": {"line": 6}}},  # if, 2 arms
        },
        "b": {"0": [1, 0, 1], "1": [1, 1]},  # switch middle arm uncovered
    }
    lcov = tmp_path / "cov.lcov"
    lcov.write_text(
        "SF:pkg/x.ts\n"
        "DA:2,1\nDA:3,0\nDA:4,1\nDA:6,1\nDA:7,1\n"
        "BRDA:4,0,0,1\nBRDA:4,0,1,0\nBRDA:4,0,2,1\n"
        "BRDA:6,1,0,1\nBRDA:6,1,1,1\n"
        "end_of_record\n",
        encoding="utf-8",
    )
    lcov_cov = load_lcov_coverage(lcov).lookup("x.ts")
    assert lcov_cov is not None
    span = FunctionSpan(start_line=1, end_line=10)
    assert coverage_for_ts_span(lcov_cov, span) == coverage_for_ts_span(istanbul, span)


def test_lcov_tolerates_real_tool_output_shape(tmp_path: Path) -> None:
    # Production LCOV carries a VER: header, per-line DA checksums (a 3rd field), FN/FNDA function
    # records, and FNF/FNH + LF/LH + BRF/BRH totals. All must be tolerated; only DA/BRDA are used.
    lcov = tmp_path / "cov.lcov"
    lcov.write_text(
        "TN:my suite\n"
        "VER:1\n"
        "SF:pkg/x.ts\n"
        "FN:1,f\nFN:5,g\nFNDA:3,f\nFNDA:0,g\nFNF:2\nFNH:1\n"
        "DA:2,3,abcdef0123456789abcdef0123456789\n"  # DA with a checksum third field
        "DA:3,0,00000000000000000000000000000000\n"
        "BRDA:2,0,0,3\nBRDA:2,0,1,0\nBRF:2\nBRH:1\n"
        "LF:2\nLH:1\n"
        "end_of_record\n",
        encoding="utf-8",
    )
    file_cov = load_lcov_coverage(lcov).lookup("x.ts")
    assert file_cov is not None
    stats = coverage_for_ts_span(file_cov, FunctionSpan(start_line=1, end_line=10))
    assert stats.line_coverage == pytest.approx(0.5)  # DA lines 2 (hit) / 3 (miss)
    assert stats.missing_lines == (3,)
    assert stats.branch_coverage == pytest.approx(0.5)  # BRDA arm 1 not taken
    assert stats.missing_branch_arms == ((2, 1),)


# ---- parser specifics ------------------------------------------------------------------


def test_lcov_da_becomes_one_statement_per_line() -> None:
    # DA lines map to synthetic statement-start lines; a bare line span picks exactly one up.
    data = load_lcov_coverage(LCOV_FILE)
    file_cov = data.lookup("sample.ts")
    assert file_cov is not None
    # Line 11 alone: measured and uncovered.
    only_11 = coverage_for_ts_span(file_cov, FunctionSpan(start_line=11, end_line=11))
    assert only_11.line_coverage == 0.0
    assert only_11.missing_lines == (11,)


def test_lcov_brda_dash_taken_counts_as_uncovered(tmp_path: Path) -> None:
    lcov = tmp_path / "cov.lcov"
    lcov.write_text(
        "SF:pkg/x.ts\nDA:2,1\nBRDA:2,0,0,1\nBRDA:2,0,1,-\nend_of_record\n",
        encoding="utf-8",
    )
    file_cov = load_lcov_coverage(lcov).lookup("x.ts")
    assert file_cov is not None
    stats = coverage_for_ts_span(file_cov, FunctionSpan(start_line=1, end_line=5))
    # `-` means the arm was never evaluated → treated as uncovered (arm index 1).
    assert stats.branch_coverage == pytest.approx(0.5)
    assert stats.missing_branch_arms == ((2, 1),)


def test_lcov_groups_arms_by_line_and_block(tmp_path: Path) -> None:
    # Two distinct branch blocks on the same line stay separate branch points (denominator 4).
    lcov = tmp_path / "cov.lcov"
    lcov.write_text(
        "SF:pkg/x.ts\n"
        "DA:3,1\n"
        "BRDA:3,0,0,1\nBRDA:3,0,1,0\n"  # block 0: one arm missing
        "BRDA:3,1,0,1\nBRDA:3,1,1,1\n"  # block 1: fully covered
        "end_of_record\n",
        encoding="utf-8",
    )
    file_cov = load_lcov_coverage(lcov).lookup("x.ts")
    assert file_cov is not None
    stats = coverage_for_ts_span(file_cov, FunctionSpan(start_line=1, end_line=5))
    assert stats.branch_coverage == pytest.approx(3 / 4)


def test_lcov_tolerates_blank_and_unknown_lines(tmp_path: Path) -> None:
    lcov = tmp_path / "cov.lcov"
    lcov.write_text(
        "TN:suite\n\nSF:pkg/x.ts\nFN:1,f\nFNDA:1,f\n\nDA:2,1\nLF:1\nLH:1\nend_of_record\n",
        encoding="utf-8",
    )
    file_cov = load_lcov_coverage(lcov).lookup("x.ts")
    assert file_cov is not None
    stats = coverage_for_ts_span(file_cov, FunctionSpan(start_line=1, end_line=5))
    assert stats.line_coverage == 1.0  # FN/FNDA/LF/LH ignored, single DA measured & covered


def test_lcov_malformed_data_lines_are_skipped(tmp_path: Path) -> None:
    lcov = tmp_path / "cov.lcov"
    lcov.write_text(
        "SF:pkg/x.ts\nDA:notanumber,1\nDA:2,1\nBRDA:2,0,0,notanumber\nend_of_record\n",
        encoding="utf-8",
    )
    file_cov = load_lcov_coverage(lcov).lookup("x.ts")
    assert file_cov is not None
    stats = coverage_for_ts_span(file_cov, FunctionSpan(start_line=1, end_line=5))
    # The bad DA and bad BRDA are dropped; the one good DA (line 2) stands.
    assert stats.line_coverage == 1.0
    assert stats.branch_coverage is None


def test_lcov_all_corrupt_da_record_is_rejected_not_silently_100_percent(tmp_path: Path) -> None:
    # A record whose DA lines were ALL unparseable must not become an empty ("100% covered") entry.
    # With only that one corrupt record, the whole file has no readable data → ValueError → skipped.
    lcov = tmp_path / "cov.lcov"
    lcov.write_text("SF:pkg/x.ts\nDA:bad,bad\nDA:also,bad\nend_of_record\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no readable coverage data"):
        load_lcov_coverage(lcov)


def test_lcov_corrupt_record_dropped_but_good_record_kept(tmp_path: Path) -> None:
    # In a multi-file report, one all-corrupt record is dropped while a good one survives — the
    # corrupt file simply has no entry (reported as unmeasured downstream), not a false 100%.
    lcov = tmp_path / "cov.lcov"
    lcov.write_text(
        "SF:pkg/corrupt.ts\nDA:bad,bad\nend_of_record\nSF:pkg/good.ts\nDA:2,1\nend_of_record\n",
        encoding="utf-8",
    )
    data = load_lcov_coverage(lcov)
    assert data.lookup("corrupt.ts") is None  # not fabricated as fully covered
    assert data.lookup("good.ts") is not None


def test_lcov_strips_utf8_bom(tmp_path: Path) -> None:
    lcov = tmp_path / "cov.lcov"
    lcov.write_bytes(b"\xef\xbb\xbfSF:pkg/x.ts\nDA:2,1\nend_of_record\n")  # UTF-8 BOM prefix
    file_cov = load_lcov_coverage(lcov).lookup("x.ts")
    assert file_cov is not None
    assert coverage_for_ts_span(file_cov, FunctionSpan(start_line=1, end_line=5)).line_coverage == 1.0


def test_lcov_flushes_record_without_trailing_end_of_record(tmp_path: Path) -> None:
    lcov = tmp_path / "cov.lcov"
    lcov.write_text("SF:pkg/x.ts\nDA:2,1\n", encoding="utf-8")  # no end_of_record
    file_cov = load_lcov_coverage(lcov).lookup("x.ts")
    assert file_cov is not None
    assert coverage_for_ts_span(file_cov, FunctionSpan(start_line=1, end_line=5)).line_coverage == 1.0


def test_lcov_parses_crlf_line_endings(tmp_path: Path) -> None:
    # Windows LCOV files use CRLF; splitlines()+strip() must handle them transparently.
    lcov = tmp_path / "cov.lcov"
    lcov.write_bytes(b"SF:pkg/x.ts\r\nDA:2,1\r\nDA:3,0\r\nend_of_record\r\n")
    file_cov = load_lcov_coverage(lcov).lookup("x.ts")
    assert file_cov is not None
    stats = coverage_for_ts_span(file_cov, FunctionSpan(start_line=1, end_line=5))
    assert stats.line_coverage == pytest.approx(0.5)
    assert stats.missing_lines == (3,)


def test_lcov_absolute_sf_path_resolves_by_suffix(tmp_path: Path) -> None:
    # Real LCOV emits absolute source paths; discovery looks up repo-relative paths, so the
    # basename/longest-suffix lookup is the load-bearing bridge. Backslashes normalize too.
    lcov = tmp_path / "cov.lcov"
    lcov.write_text(
        "SF:/home/ci/project/src/app/sample.ts\nDA:2,1\nend_of_record\n"
        "SF:C:\\win\\project\\src\\app\\other.ts\nDA:2,1\nend_of_record\n",
        encoding="utf-8",
    )
    data = load_lcov_coverage(lcov)
    assert data.lookup("src/app/sample.ts") is not None
    assert data.lookup("sample.ts") is not None
    assert data.lookup("other.ts") is not None  # backslash path normalized to '/'


def test_lcov_sf_record_with_no_data_is_an_empty_measured_entry(tmp_path: Path) -> None:
    # An SF record with no DA/BRDA is a valid (present, nothing-to-measure) entry, not a load
    # error — mirrors an empty Istanbul `{"statementMap": {}, "s": {}}` file.
    lcov = tmp_path / "cov.lcov"
    lcov.write_text("SF:pkg/x.ts\nend_of_record\n", encoding="utf-8")
    file_cov = load_lcov_coverage(lcov).lookup("x.ts")
    assert file_cov is not None
    stats = coverage_for_ts_span(file_cov, FunctionSpan(start_line=1, end_line=5))
    assert stats.line_coverage == 1.0  # nothing to exercise → fully covered
    assert stats.branch_coverage is None


def test_lcov_lookup_matches_by_basename_and_suffix() -> None:
    data = load_lcov_coverage(LCOV_FILE)
    assert data.lookup("src/app/sample.ts") is not None
    assert data.lookup("app/sample.ts") is not None
    assert data.lookup("sample.ts") is not None
    assert data.lookup("nope.ts") is None


def test_lcov_spans_cover_any_statement_detects_alignment() -> None:
    file_cov = _lcov_file_cov()
    assert spans_cover_any_statement(file_cov, [COVERED_SPAN, PARTIAL_SPAN]) is True
    assert spans_cover_any_statement(file_cov, [FunctionSpan(start_line=200, end_line=210)]) is False


# ---- loader error contract -------------------------------------------------------------


def test_load_lcov_missing_file_raises_filenotfound(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_lcov_coverage(tmp_path / "absent.lcov")


def test_load_lcov_without_sf_records_raises_valueerror(tmp_path: Path) -> None:
    bad = tmp_path / "cov.lcov"
    bad.write_text("TN:suite\nnot an lcov body\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no SF: records"):
        load_lcov_coverage(bad)


# ---- format-dispatching loader (load_ts_coverage_files) --------------------------------


def test_dispatcher_detects_lcov_by_extension() -> None:
    data = load_ts_coverage_files([LCOV_FILE])
    assert data.lookup("sample.ts") is not None


def test_dispatcher_detects_istanbul_json_by_extension() -> None:
    data = load_ts_coverage_files([ISTANBUL_FILE])
    assert data.lookup("sample.ts") is not None


def test_dispatcher_mixes_istanbul_and_lcov(tmp_path: Path) -> None:
    # One LCOV shard measuring sample.ts, one Istanbul shard measuring an unrelated file: merged.
    lcov = tmp_path / "a.lcov"
    lcov.write_text(LCOV_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    istanbul = tmp_path / "b.json"
    istanbul.write_text(json.dumps({"pkg-b/other.ts": {"statementMap": {}, "s": {}}}), encoding="utf-8")
    data = load_ts_coverage_files([lcov, istanbul])
    assert data.lookup("sample.ts") is not None  # from the LCOV shard
    assert data.lookup("other.ts") is not None  # from the Istanbul shard


def test_dispatcher_content_sniffs_unknown_extension(tmp_path: Path) -> None:
    # A file with a neutral extension is routed by its first non-blank line.
    lcov_txt = tmp_path / "coverage.txt"
    lcov_txt.write_text(LCOV_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    json_txt = tmp_path / "istanbul.txt"
    json_txt.write_text(ISTANBUL_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    assert load_ts_coverage_files([lcov_txt]).lookup("sample.ts") is not None
    assert load_ts_coverage_files([json_txt]).lookup("sample.ts") is not None


def test_dispatcher_extension_wins_over_content_with_a_directed_hint(tmp_path: Path) -> None:
    # Design decision: the extension is authoritative. A file with LCOV content but a `.json`
    # extension is routed to the Istanbul loader — but instead of the opaque "not a JSON object"
    # error, the user gets a directed hint that it looks like LCOV. Skipped via on_error, not
    # silently misparsed. (Content sniffing only kicks in for a neutral extension.)
    mislabeled = tmp_path / "cov.json"
    mislabeled.write_text(LCOV_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    messages: list[str] = []
    data = load_ts_coverage_files([mislabeled], on_error=lambda path, msg: messages.append(msg))
    assert data.lookup("sample.ts") is None
    assert any("looks like an LCOV report" in m for m in messages)


def test_dispatcher_skips_bad_shard_with_callback(tmp_path: Path) -> None:
    good = tmp_path / "good.lcov"
    good.write_text(LCOV_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    errors: list[str] = []
    data = load_ts_coverage_files(
        [tmp_path / "absent.lcov", good],
        on_error=lambda path, msg: errors.append(str(path)),
    )
    assert data.lookup("sample.ts") is not None  # good shard still loaded
    assert any("absent.lcov" in e for e in errors)


# ---- CLI wiring (needs the typescript extra) ------------------------------------------


def _isolated_lcov_app(tmp_path: Path) -> Path:
    """Copy the app fixture (source + LCOV report) into a tmp dir outside the repo, so config
    discovery doesn't walk up to riskratchet's own `[tool.riskratchet]` exclude."""
    dest = tmp_path / "app"
    dest.mkdir()
    (dest / "sample.ts").write_text((APP / "sample.ts").read_text(encoding="utf-8"), encoding="utf-8")
    (dest / "coverage.lcov").write_text(LCOV_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    return dest


def test_scan_ts_coverage_lcov_annotates_stderr_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    from typer.testing import CliRunner

    from riskratchet.cli import app

    runner = CliRunner()
    monkeypatch.chdir(_isolated_lcov_app(tmp_path))
    result = runner.invoke(
        app,
        ["scan", ".", "--experimental-typescript", "--ts-coverage", "coverage.lcov", "--no-auto-cov"],
    )
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert "cov 100% line" in result.stderr  # covered()
    assert "cov 80% line / 50% branch" in result.stderr  # partial()
    assert "miss-lines 11" in result.stderr


def test_scan_ts_coverage_lcov_embedded_in_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    from typer.testing import CliRunner

    from riskratchet.cli import app

    runner = CliRunner()
    monkeypatch.chdir(_isolated_lcov_app(tmp_path))
    result = runner.invoke(
        app,
        [
            "scan",
            ".",
            "--experimental-typescript",
            "--ts-coverage",
            "coverage.lcov",
            "--json",
            "--no-auto-cov",
        ],
    )
    assert result.exit_code == 0, (result.stdout, result.stderr)
    payload = json.loads(result.stdout)
    by_name = {fn["qualname"]: fn for fn in payload["typescript"]}
    assert by_name["covered"]["line_coverage"] == 1.0
    assert by_name["partial"]["line_coverage"] == 0.8
    assert by_name["partial"]["branch_coverage"] == 0.5


def test_scan_mixes_istanbul_and_lcov_reports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A single --ts-coverage list may mix formats: LCOV for sample.ts + an unrelated Istanbul
    shard both load, and the function is annotated from the LCOV report."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    from typer.testing import CliRunner

    from riskratchet.cli import app

    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "sample.ts").write_text((APP / "sample.ts").read_text(encoding="utf-8"), encoding="utf-8")
    (app_dir / "a.lcov").write_text(LCOV_FILE.read_text(encoding="utf-8"), encoding="utf-8")
    (app_dir / "b.json").write_text(
        json.dumps({"pkg-b/other.ts": {"statementMap": {}, "s": {}}}), encoding="utf-8"
    )
    monkeypatch.chdir(app_dir)
    result = CliRunner().invoke(
        app,
        [
            "scan",
            ".",
            "--experimental-typescript",
            "--no-auto-cov",
            "--ts-coverage",
            "a.lcov",
            "--ts-coverage",
            "b.json",
        ],
    )
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert "cov 100% line" in result.stderr  # sample.ts::covered resolved via the LCOV report


def _run_ts_scan(app_dir: Path, monkeypatch: pytest.MonkeyPatch, *extra: str) -> Any:
    from typer.testing import CliRunner

    from riskratchet.cli import app

    monkeypatch.chdir(app_dir)
    return CliRunner().invoke(app, ["scan", ".", "--experimental-typescript", "--no-auto-cov", *extra])


def test_scan_ts_coverage_lcov_warns_and_omits_when_misaligned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An LCOV report whose line numbers describe compiled JS (no DA lands in any discovered span)
    must warn and show NO coverage, not confidently-wrong numbers — same guard as Istanbul."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "sample.ts").write_text((APP / "sample.ts").read_text(encoding="utf-8"), encoding="utf-8")
    # Same measured lines as the fixture but shoved past line 200, as if collected on built output.
    shifted = "SF:src/app/sample.ts\n" + "".join(
        f"DA:{200 + n},{hits}\n" for n, hits in ((2, 1), (3, 1), (7, 1), (8, 1), (9, 1), (11, 0), (13, 1))
    )
    shifted += "BRDA:208,0,0,1\nBRDA:208,0,1,0\nend_of_record\n"
    (app_dir / "shifted.lcov").write_text(shifted, encoding="utf-8")

    result = _run_ts_scan(app_dir, monkeypatch, "--ts-coverage", "shifted.lcov")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert "don't intersect any discovered function" in result.stderr
    assert "% line" not in result.stderr  # no coverage annotation emitted at all


def test_scan_ts_coverage_lcov_hints_when_file_has_no_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A discovered file absent from the LCOV report is reported explicitly, not silently."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "other.ts").write_text("export function f(): number {\n  return 1;\n}\n", encoding="utf-8")
    # An LCOV that measures some unrelated file, so other.ts has no entry.
    (app_dir / "cov.lcov").write_text("SF:pkg/unrelated.ts\nDA:1,1\nend_of_record\n", encoding="utf-8")

    result = _run_ts_scan(app_dir, monkeypatch, "--ts-coverage", "cov.lcov")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert "1 file(s) had no coverage entry" in result.stderr
