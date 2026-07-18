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
def test_lcov_yields_identical_coverage_stats_to_istanbul(span: FunctionSpan) -> None:
    # The whole design rests on this: same measured lines/branches → same CoverageStats.
    istanbul = load_istanbul_coverage(ISTANBUL_FILE).lookup("sample.ts")
    lcov = load_lcov_coverage(LCOV_FILE).lookup("sample.ts")
    assert istanbul is not None and lcov is not None
    assert coverage_for_ts_span(lcov, span) == coverage_for_ts_span(istanbul, span)


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
