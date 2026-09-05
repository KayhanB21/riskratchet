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


def test_analyze_emits_signature_fingerprint(tmp_path: Path) -> None:
    """Every analyzed function carries a non-empty signature fingerprint."""
    _write(tmp_path, "m.py", "def helper(x: int) -> int:\n    return x\n")
    report = analyze([tmp_path], root=tmp_path, use_git=False)
    [fn] = report.functions
    assert fn.signature is not None
    assert len(fn.signature) == 64  # sha256 hex


def test_analyze_with_coverage_map_uses_per_prefix_coverage(tmp_path: Path) -> None:
    """coverage_map dispatches each file to its declared shard."""
    (tmp_path / "packages" / "a").mkdir(parents=True)
    (tmp_path / "packages" / "b").mkdir(parents=True)
    _write(tmp_path / "packages" / "a", "core.py", "def fa(x): return x\n")
    _write(tmp_path / "packages" / "b", "core.py", "def fb(x): return x\n")
    cov_a = tmp_path / "cov-a.json"
    cov_b = tmp_path / "cov-b.json"
    cov_a.write_text(
        json.dumps({"files": {"packages/a/core.py": {"executed_lines": [1], "missing_lines": []}}}),
        encoding="utf-8",
    )
    cov_b.write_text(
        json.dumps({"files": {"packages/b/core.py": {"executed_lines": [], "missing_lines": [1]}}}),
        encoding="utf-8",
    )
    report = analyze(
        [tmp_path / "packages" / "a", tmp_path / "packages" / "b"],
        root=tmp_path,
        coverage_map={"packages/a": cov_a, "packages/b": cov_b},
        use_git=False,
    )
    by_name = {fn.id.qualname: fn for fn in report.functions}
    assert by_name["fa"].coverage.line_coverage == 1.0
    assert by_name["fb"].coverage.line_coverage == 0.0
    assert report.coverage_status == "present"


def test_analyze_rejects_both_coverage_path_and_coverage_map(tmp_path: Path) -> None:
    """Passing both is a programming error and must raise."""
    _write(tmp_path, "m.py", "def f(): return 1\n")
    cov = tmp_path / "c.json"
    cov.write_text('{"files": {"m.py": {"executed_lines": []}}}', encoding="utf-8")
    with pytest.raises(ValueError, match="mutually exclusive"):
        analyze(
            [tmp_path],
            root=tmp_path,
            coverage_path=cov,
            coverage_map={"m.py": cov},
            use_git=False,
        )


def test_analyze_multi_root_paths_stay_repo_relative(tmp_path: Path) -> None:
    """Scanning multiple package roots keeps repo-relative POSIX paths."""
    (tmp_path / "packages" / "a").mkdir(parents=True)
    (tmp_path / "packages" / "b").mkdir(parents=True)
    _write(tmp_path / "packages" / "a", "core.py", "def fa(): return 1\n")
    _write(tmp_path / "packages" / "b", "core.py", "def fb(): return 1\n")
    report = analyze(
        [tmp_path / "packages" / "a", tmp_path / "packages" / "b"],
        root=tmp_path,
        use_git=False,
    )
    paths = {fn.id.path for fn in report.functions}
    assert paths == {"packages/a/core.py", "packages/b/core.py"}


# --- 0.3.6: generated files are skipped, counted, and listed ---------------------------
#
# The TypeScript backend has honoured a comment-anchored `@generated` header since
# 0.2.12 and its docstring claimed the Python backend mirrored it. It did not: a
# `# @generated` file was scored in full. Now both backends skip its functions, count
# it, and list it with zero functions, so a dropped population is never invisible.


def test_a_generated_python_file_is_skipped_counted_and_listed(tmp_path: Path) -> None:
    _write(
        tmp_path, "gen.py", "# @generated by protoc-gen-something. DO NOT EDIT.\ndef made(a):\n    return a\n"
    )
    _write(tmp_path, "real.py", "def kept(a):\n    return a\n")

    report = analyze([tmp_path], root=tmp_path, use_git=False)

    assert [fn.id.qualname for fn in report.functions] == ["kept"]
    assert report.skipped_generated_files == 1
    listed = {stats.path: stats for stats in report.files}
    assert listed["gen.py"].function_count == 0
    assert listed["gen.py"].total_lines == 3
    assert listed["real.py"].function_count == 1


@pytest.mark.parametrize(
    "source",
    [
        '"""Docs mention @generated in prose."""\ndef f():\n    return 1\n',
        'MARK = "@generated"\ndef f():\n    return 1\n',
        "def f():\n    return 1  # @generated later, not a header\n",
    ],
)
def test_generated_marker_must_be_a_comment_anchored_header(tmp_path: Path, source: str) -> None:
    _write(tmp_path, "m.py", source)

    report = analyze([tmp_path], root=tmp_path, use_git=False)

    assert [fn.id.qualname for fn in report.functions] == ["f"]
    assert report.skipped_generated_files == 0


def test_a_file_that_fails_to_parse_is_still_listed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`files` means every file the scan reached: a syntax-error file appears with zero
    functions (as the TypeScript backend already did), not nowhere."""
    _write(tmp_path, "broken.py", "def f(:\n    pass\nx = 1\n")
    _write(tmp_path, "ok.py", "def g():\n    return 1\n")

    report = analyze([tmp_path], root=tmp_path, use_git=False)

    listed = {stats.path: stats for stats in report.files}
    assert listed["broken.py"].function_count == 0
    assert listed["broken.py"].total_lines == 3
    assert report.skipped_generated_files == 0
    assert "skipping" in capsys.readouterr().err
