"""0.3.11: an Istanbul report that matches no scanned TypeScript file says so.

0.3.10 added the note for Python and counted Python files only. A TypeScript report keyed
to some other tree printed one stderr line, and every stdout format stayed silent.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from riskratchet.cli import app
from riskratchet.models import RiskReport

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_typescript")

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()

_TS = (
    "export function f(x: number, y: number): number {\n"
    "  if (x > 1) {\n"
    "    if (y > 2) { return 1; }\n"
    "    return 2;\n"
    "  }\n"
    "  return y;\n"
    "}\n"
)
_PY = "def g(x):\n    if x:\n        return 1\n    return 0\n"
_NOTE = "TypeScript coverage matched 0 of 2 scanned files"


def _istanbul(*paths: str) -> str:
    statement = {"start": {"line": 2, "column": 0}, "end": {"line": 2, "column": 5}}
    return json.dumps(
        {
            path: {
                "path": path,
                "statementMap": {"0": statement},
                "s": {"0": 1},
                "fnMap": {},
                "f": {},
                "branchMap": {},
                "b": {},
            }
            for path in paths
        }
    )


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *covered: str, python: bool = False) -> None:
    """Two TypeScript files and an Istanbul report that names `covered`."""
    (tmp_path / "src").mkdir()
    for name in ("a.ts", "b.ts"):
        (tmp_path / "src" / name).write_text(_TS, encoding="utf-8")
    (tmp_path / "cov.json").write_text(_istanbul(*covered), encoding="utf-8")
    config = '[tool.riskratchet]\npaths = ["src"]\nauto_coverage = false\ntypescript = true\nts_coverage = ["cov.json"]\n'
    if python:
        (tmp_path / "src" / "g.py").write_text(_PY, encoding="utf-8")
        (tmp_path / "coverage.json").write_text(
            json.dumps({"files": {"src/g.py": {"executed_lines": [1, 2, 3, 4], "missing_lines": []}}}),
            encoding="utf-8",
        )
        config += 'coverage = "coverage.json"\n'
    (tmp_path / "pyproject.toml").write_text(config, encoding="utf-8")
    monkeypatch.chdir(tmp_path)


def _run(*args: str) -> tuple[int, str, str]:
    result = runner.invoke(app, [*args, "--no-git"])
    return result.exit_code, " ".join(result.stdout.split()), result.stderr


def test_scan_says_so_in_every_format_and_once_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path, monkeypatch, "/elsewhere/other.ts")

    for fmt in ("table", "markdown"):
        code, out, err = _run("scan", "--format", fmt)
        assert code == 0, (fmt, out, err)
        assert _NOTE in out, fmt
        assert err.count(f"warning: {_NOTE}") == 1, fmt


def test_check_says_so_on_the_baseline_line_of_every_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path, monkeypatch, "/elsewhere/other.ts")
    assert _run("baseline")[0] == 0

    for fmt in ("table", "markdown", "pr-comment"):
        code, out, err = _run("check", "--format", fmt)
        assert code == 0, (fmt, out, err)
        assert f"0 not seen this run · {_NOTE}" in out, fmt
        assert err.count(f"warning: {_NOTE}") == 1, fmt


def test_a_partial_match_says_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path, monkeypatch, "src/a.ts")
    assert _run("baseline")[0] == 0

    for args in (("scan",), ("check",), ("check", "--format", "pr-comment")):
        _, out, err = _run(*args)
        assert "matched 0 of" not in out + err, args


def test_python_that_matches_does_not_hide_typescript_that_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two counts are kept apart: summed, 1 match of 3 files would read as a partial match."""
    _project(tmp_path, monkeypatch, "/elsewhere/other.ts", python=True)

    _, out, err = _run("scan")

    assert _NOTE in out
    assert "warning: coverage matched 0 of" not in err
    assert "no TypeScript function has coverage data" in err


def test_the_note_names_no_path_under_private_comment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path, monkeypatch, "/elsewhere/other.ts")
    assert _run("baseline")[0] == 0

    result = runner.invoke(
        app,
        ["check", "--no-git", "--format", "pr-comment", "--private-comment", "--redact-salt", "s"],
    )

    assert _NOTE in result.stdout
    assert "src/a.ts" not in result.stdout
    warning = next(line for line in result.stderr.splitlines() if "matched 0 of" in line)
    assert "src/" not in warning
    assert "elsewhere" not in warning


def test_both_notes_join_when_both_backends_match_nothing() -> None:
    report = RiskReport(
        functions=(),
        files=(),
        coverage_checked_files=1,
        coverage_unmatched_files=1,
        ts_coverage_checked_files=2,
        ts_coverage_unmatched_files=2,
    )

    assert report.coverage_note() == f"coverage matched 0 of 1 scanned file; {_NOTE}"
    warning = report.coverage_warning()
    assert warning is not None
    assert "no function has coverage data" in warning
    assert "no TypeScript function has coverage data" in warning
