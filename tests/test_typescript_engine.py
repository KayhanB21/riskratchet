"""Scored TypeScript backend + report merge + orchestration seam (B3, 0.3.0).

Covers `typescript_engine.analyze_typescript` / `merge_reports` and `pipeline.build_report`: TS
functions are scored with `language="typescript"` and the TS complexity calibration, coverage flows
into the score, the merge is a pure Python-first append, the Python-only path is byte-identical to
`engine.analyze`, and the import-isolation invariant holds (a Python-only build never imports the
TypeScript backend or tree-sitter).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_typescript")

from riskratchet import pipeline
from riskratchet import typescript_engine as te
from riskratchet.coverage import MissingCoveragePolicy, coverage_for_span
from riskratchet.engine import analyze
from riskratchet.models import FunctionSpan
from riskratchet.scoring import TYPESCRIPT_COMPLEXITY_CALIBRATION, structural_complexity_score

APP = Path(__file__).parent / "fixtures" / "typescript" / "app"
SPAN = FunctionSpan(start_line=1, end_line=4)


def test_analyze_typescript_scores_with_language_tag() -> None:
    report = te.analyze_typescript([APP], root=APP, use_git=False)
    assert len(report.functions) == 2
    assert all(fn.language == "typescript" for fn in report.functions)
    assert all(fn.score >= 0.0 for fn in report.functions)
    # FileStats is synthesized (real line count, per-file function count).
    assert report.files and report.files[0].total_lines > 0


def test_analyze_typescript_uses_ts_complexity_calibration() -> None:
    report = te.analyze_typescript([APP], root=APP, use_git=False)
    partial = next(fn for fn in report.functions if fn.id.qualname == "partial")
    # The structural component must be computed with the TS band, not (accidentally) something else.
    assert partial.components.structural_complexity == structural_complexity_score(
        partial.complexity, TYPESCRIPT_COMPLEXITY_CALIBRATION
    )


def test_analyze_typescript_coverage_flows_into_score() -> None:
    report = te.analyze_typescript([APP], root=APP, use_git=False, ts_coverage_paths=[APP / "coverage.lcov"])
    assert report.coverage_status == "present"
    by_name = {fn.id.qualname: fn for fn in report.functions}
    assert by_name["covered"].coverage.line_coverage == 1.0
    assert by_name["covered"].score == 0.0
    assert by_name["partial"].coverage.line_coverage == pytest.approx(0.80)
    assert by_name["partial"].score > 0.0


def test_analyze_typescript_missing_coverage_policies() -> None:
    # No coverage report at all: PESSIMISTIC treats every function as 0% (default).
    pess = te.analyze_typescript([APP], root=APP, use_git=False)
    assert all(fn.coverage.line_coverage == 0.0 for fn in pess.functions)
    # OPTIMISTIC treats unmeasured as fully covered.
    opt = te.analyze_typescript(
        [APP], root=APP, use_git=False, missing_coverage_policy=MissingCoveragePolicy.OPTIMISTIC
    )
    assert all(fn.coverage.line_coverage == 1.0 for fn in opt.functions)


def test_merge_reports_appends_python_first(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def f(x):\n    return x + 1\n", encoding="utf-8")
    (tmp_path / "s.ts").write_text("export function g(x: number) { return x + 1; }\n", encoding="utf-8")
    py = analyze([tmp_path], root=tmp_path, use_git=False)
    ts = te.analyze_typescript([tmp_path], root=tmp_path, use_git=False)
    merged = te.merge_reports(py, ts)
    assert merged.functions == py.functions + ts.functions  # Python first, then TS, pure append
    assert [fn.language for fn in merged.functions] == ["python", "typescript"]
    # Ids never collide across languages (.py vs .ts paths).
    assert len({fn.id for fn in merged.functions}) == len(merged.functions)


def test_build_report_python_only_is_byte_identical_to_analyze(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def f(x):\n    if x:\n        return 1\n    return 0\n", encoding="utf-8")
    assert pipeline.build_report([tmp_path], root=tmp_path, use_git=False) == analyze(
        [tmp_path], root=tmp_path, use_git=False
    )


def test_build_report_merges_typescript(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("def f(x):\n    return x\n", encoding="utf-8")
    (tmp_path / "s.ts").write_text("export function g() { return 1; }\n", encoding="utf-8")
    report = pipeline.build_report([tmp_path], root=tmp_path, use_git=False, typescript=True)
    assert {fn.language for fn in report.functions} == {"python", "typescript"}


def test_import_isolation_python_only_never_imports_tree_sitter(tmp_path: Path) -> None:
    # A Python-only build_report must not import the TypeScript backend or tree-sitter — the
    # mechanical guarantee behind the "no mandatory Node dependency" non-goal. Run in a fresh
    # interpreter so no other test's imports pollute sys.modules.
    (tmp_path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    script = dedent(
        f"""
        import sys
        from pathlib import Path
        from riskratchet import pipeline
        root = Path({str(tmp_path)!r})
        pipeline.build_report([root], root=root, use_git=False)
        assert "tree_sitter" not in sys.modules, "tree_sitter leaked into the Python-only path"
        assert "riskratchet.typescript_engine" not in sys.modules, "typescript_engine leaked"
        print("clean")
        """
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout


# --- 0.3.5: the two backends must score an absent report identically ----------------


@pytest.mark.parametrize("policy", list(MissingCoveragePolicy))
def test_an_absent_report_scores_the_same_in_both_backends(policy: MissingCoveragePolicy) -> None:
    """`analyze_typescript`'s docstring claims this parity; SKIP broke it.

    SKIP fell through to the OPTIMISTIC branch, so a `--typescript` run with no TS
    report scored every function 100% covered while Python scored the same situation
    0% — zeroing `coverage_gap` and `public_surface`, 40% of the weight, for TypeScript
    only. Parametrizing over the whole enum is what stops a fourth policy landing on
    one backend alone.
    """
    ts_stats = te._resolve_coverage(None, has_coverage=False, policy=policy)
    py_stats = coverage_for_span(None, SPAN, missing_policy=policy)

    assert ts_stats is not None, "an absent *report* must never drop a function"
    assert ts_stats.line_coverage == py_stats.line_coverage


def test_skip_only_drops_a_function_when_a_report_actually_loaded() -> None:
    """SKIP means "this file is not measured by a report I have", not "I have no report"."""
    assert te._resolve_coverage(None, has_coverage=True, policy=MissingCoveragePolicy.SKIP) is None
    assert te._resolve_coverage(None, has_coverage=False, policy=MissingCoveragePolicy.SKIP) is not None


def test_an_unreadable_report_is_fatal_rather_than_an_empty_coverage_view(tmp_path: Path) -> None:
    """`has_coverage` used to key off the *request*, so an unreadable report read as
    "a report that measured nothing" — and under SKIP that dropped every function."""
    junk = tmp_path / "coverage-final.json"
    junk.write_text("not json", encoding="utf-8")

    with pytest.raises(ValueError):
        te.analyze_typescript(
            [APP],
            root=APP,
            use_git=False,
            ts_coverage_paths=[junk],
            missing_coverage_policy=MissingCoveragePolicy.SKIP,
        )


@pytest.mark.parametrize(
    ("pattern", "suppresses"),
    [
        ("lib/app.ts::handler", True),  # the canonical target form
        ("lib/**", True),
        ("handler", True),
        ("lib/app.ts::other", False),
        ("nomatch", False),
    ],
)
def test_both_backends_suppress_the_same_patterns(pattern: str, suppresses: bool) -> None:
    """`allow` semantics must not depend on which backend found the function.

    The `::` case is the one that mattered: it matched nothing in either backend, so
    a target copied out of a report silently suppressed nothing while the user believed
    that debt was parked.
    """
    from riskratchet.engine import pattern_matches

    assert pattern_matches(pattern, "lib/app.ts::handler", "lib/app.ts", "handler") is suppresses
    assert te._is_allowed("lib/app.ts", "handler", [pattern]) is suppresses


# --- 0.3.6: generated files are counted and disclosed, in both backends ----------------


def _write_ts_project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "gen.ts").write_text(
        "// @generated by some-tool. DO NOT EDIT.\nexport function made(a: number): number { return a; }\n",
        encoding="utf-8",
    )
    (root / "src" / "lib.ts").write_text(
        "export function kept(a: number): number { return a; }\n", encoding="utf-8"
    )
    (root / "src" / "app.py").write_text("# @generated\ndef made():\n    return 1\n", encoding="utf-8")
    (root / "src" / "real.py").write_text("def kept():\n    return 1\n", encoding="utf-8")
    return root


def test_a_generated_typescript_file_is_skipped_counted_and_listed(tmp_path: Path) -> None:
    root = _write_ts_project(tmp_path)

    report = te.analyze_typescript([root / "src"], root=root, use_git=False)

    assert [fn.id.qualname for fn in report.functions] == ["kept"]
    assert report.skipped_generated_files == 1
    listed = {stats.path: stats for stats in report.files}
    assert listed["src/gen.ts"].function_count == 0
    assert listed["src/gen.ts"].total_lines == 2


def test_merge_reports_sums_the_generated_count_and_the_summary_says_so(tmp_path: Path) -> None:
    from riskratchet.reporting.summary import _summary_line, _summary_payload

    root = _write_ts_project(tmp_path)

    report = pipeline.build_report([root / "src"], root=root, use_git=False, typescript=True)

    assert report.skipped_generated_files == 2  # one per language
    assert sorted(fn.id.qualname for fn in report.functions) == ["kept", "kept"]
    assert _summary_payload(report)["skipped_generated_files"] == 2
    assert _summary_payload(report)["total_files"] == 4
    assert "2 generated files skipped" in _summary_line(report)


def test_node_modules_is_not_scanned_unless_named(tmp_path: Path) -> None:
    from riskratchet.typescript import iter_typescript_files

    root = tmp_path / "proj"
    dep = root / "src" / "node_modules" / "dep"
    dep.mkdir(parents=True)
    (dep / "index.ts").write_text("export function dep(): number { return 1; }\n", encoding="utf-8")
    (root / "src" / "own.ts").write_text("export function own(): number { return 1; }\n", encoding="utf-8")

    assert [p.name for p in iter_typescript_files([root / "src"], root=root)] == ["own.ts"]
    # Named explicitly, a dependency tree is honoured — the walk only skips what it descends into.
    assert [p.name for p in iter_typescript_files([dep], root=root)] == ["index.ts"]
    report = te.analyze_typescript([root / "src"], root=root, use_git=False)
    assert [fn.id.qualname for fn in report.functions] == ["own"]
