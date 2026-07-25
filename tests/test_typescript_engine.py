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
from riskratchet.coverage import MissingCoveragePolicy
from riskratchet.engine import analyze
from riskratchet.scoring import TYPESCRIPT_COMPLEXITY_CALIBRATION, structural_complexity_score

APP = Path(__file__).parent / "fixtures" / "typescript" / "app"


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
