"""End-to-end tests for the analyze orchestrator."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest

from riskratchet.engine import analyze


def _write(tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / name
    path.write_text(dedent(source).strip() + "\n", encoding="utf-8")
    return path


def test_analyze_produces_function_risks(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "m.py",
        """
        def trivial():
            return 1

        def branchy(x):
            if x > 0:
                return 1
            if x < 0:
                return -1
            return 0
    """,
    )
    report = analyze([tmp_path], root=tmp_path, use_git=False)
    by_name = {fn.id.qualname: fn for fn in report.functions}
    assert set(by_name.keys()) == {"trivial", "branchy"}
    assert by_name["branchy"].complexity.cyclomatic > by_name["trivial"].complexity.cyclomatic
    assert by_name["trivial"].score >= 0.0


def test_analyze_with_coverage_lowers_score(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "m.py",
        """
        def covered():
            return 1

        def uncovered():
            return 2
    """,
    )
    coverage = {
        "files": {
            "m.py": {
                "executed_lines": [2],
                "missing_lines": [5],
            }
        }
    }
    coverage_path = tmp_path / "coverage.json"
    coverage_path.write_text(json.dumps(coverage), encoding="utf-8")
    report = analyze(
        [tmp_path],
        root=tmp_path,
        coverage_path=coverage_path,
        use_git=False,
    )
    by_name = {fn.id.qualname: fn for fn in report.functions}
    assert by_name["covered"].coverage.line_coverage > 0.0
    assert by_name["uncovered"].coverage.line_coverage == 0.0
    assert by_name["uncovered"].score > by_name["covered"].score


def test_analyze_skips_files_with_syntax_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write(
        tmp_path,
        "good.py",
        """
        def ok():
            return 1
    """,
    )
    (tmp_path / "broken.py").write_text("def broken(:\n    pass\n", encoding="utf-8")
    report = analyze([tmp_path], root=tmp_path, use_git=False)
    assert {fn.id.qualname for fn in report.functions} == {"ok"}
    err = capsys.readouterr().err
    assert "broken.py" in err


def test_analyze_respects_exclude(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    _write(tmp_path / "src", "a.py", "def keep(): return 1\n")
    _write(tmp_path / "tests", "test_a.py", "def drop(): return 1\n")
    report = analyze(
        [tmp_path],
        root=tmp_path,
        use_git=False,
        exclude=["tests/**"],
    )
    assert {fn.id.qualname for fn in report.functions} == {"keep"}
