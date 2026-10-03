"""0.3.10: a coverage report that matches no scanned file says so.

`coverage_status` reads "present" whenever a report loaded. When its `files` keys name some
other tree (a per-package report, site-packages, another checkout) nothing matches, every
function scores as uncovered, and the gate stops seeing coverage change. `doctor` reported
this as `coverage-overlap`; `scan`, `baseline`, `check`, `diff`, and the plugin did not.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from riskratchet.cli import app
from riskratchet.models import RiskReport, coverage_overlap_note
from riskratchet.redaction import RedactionConfig, redact_report

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

runner = CliRunner()

_SOURCE = "def f(x):\n    if x:\n        return 1\n    return 0\n"
_NOTE = "matched 0 of 2 scanned files"


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, object]) -> Path:
    """Two scored files and a coverage report holding `files`."""
    for name in ("a.py", "b.py"):
        (tmp_path / "src").mkdir(exist_ok=True)
        (tmp_path / "src" / name).write_text(_SOURCE, encoding="utf-8")
    (tmp_path / "coverage.json").write_text(json.dumps({"files": files}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _run(*args: str) -> tuple[int, str, str]:
    result = runner.invoke(app, [*args, "--coverage", "coverage.json", "--no-git"])
    return result.exit_code, result.stdout, result.stderr


_ELSEWHERE: dict[str, object] = {"other/tree.py": {"executed_lines": [1], "missing_lines": []}}
_ONE_MATCH: dict[str, object] = {"src/a.py": {"executed_lines": [1, 2, 3, 4], "missing_lines": []}}


def test_the_note_is_none_unless_every_checked_file_is_unmatched() -> None:
    assert coverage_overlap_note(0, 0) is None
    assert coverage_overlap_note(3, 2) is None
    assert coverage_overlap_note(3, 3) == "coverage matched 0 of 3 scanned files"
    assert coverage_overlap_note(1, 1) == "coverage matched 0 of 1 scanned file"


def test_scan_says_so_in_the_summary_line_and_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path, monkeypatch, _ELSEWHERE)

    code, out, err = _run("scan", "src")

    assert code == 0
    assert _NOTE in " ".join(out.split())
    assert err.count("warning: coverage matched 0 of 2 scanned files") == 1


def test_scan_markdown_says_so_next_to_the_coverage_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path, monkeypatch, _ELSEWHERE)

    _, out, _ = _run("scan", "src", "--format", "markdown")

    assert f"**Coverage:** present, {_NOTE}" in out


def test_check_says_so_on_the_baseline_line_of_every_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path, monkeypatch, _ELSEWHERE)
    assert _run("baseline", "src")[0] == 0

    for fmt in ("table", "markdown", "pr-comment"):
        code, out, err = _run("check", "src", "--format", fmt)
        assert code == 0, (fmt, out, err)
        assert f"0 not seen this run · coverage {_NOTE}" in " ".join(out.split()), fmt
        assert err.count("warning: coverage matched 0 of 2 scanned files") == 1, fmt


def test_baseline_and_diff_warn_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path, monkeypatch, _ELSEWHERE)

    assert "warning: coverage matched 0 of 2 scanned files" in _run("baseline", "src")[2]
    assert "warning: coverage matched 0 of 2 scanned files" in _run("diff", "src")[2]


def test_a_partial_match_says_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One matched file is the ordinary state of a project with an untested module."""
    _project(tmp_path, monkeypatch, _ONE_MATCH)
    assert _run("baseline", "src")[0] == 0

    for args in (("scan", "src"), ("scan", "src", "--format", "markdown"), ("check", "src")):
        _, out, err = _run(*args)
        assert "matched 0 of" not in out + err, args


def test_the_json_summary_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The schemas do not move: `coverage_status` stays "present" and no key is added."""
    _project(tmp_path, monkeypatch, _ELSEWHERE)

    _, out, _ = _run("scan", "src", "--format", "json")
    summary = json.loads(out)["summary"]

    assert summary["coverage_status"] == "present"
    assert not [key for key in summary if "matched" in key or "unmatched" in key]


def test_the_note_names_no_path_under_private_comment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path, monkeypatch, _ELSEWHERE)
    assert _run("baseline", "src")[0] == 0

    result = runner.invoke(
        app,
        [
            "check",
            "src",
            "--coverage",
            "coverage.json",
            "--no-git",
            "--format",
            "pr-comment",
            "--private-comment",
            "--redact-salt",
            "s",
        ],
    )

    assert f"coverage {_NOTE}" in result.stdout
    assert "src/a.py" not in result.stdout
    warning = next(line for line in result.stderr.splitlines() if "coverage matched 0 of" in line)
    assert "src/" not in warning
    assert "other/tree.py" not in warning


def test_redaction_keeps_the_counts() -> None:
    report = RiskReport(functions=(), files=(), coverage_checked_files=2, coverage_unmatched_files=2)

    redacted = redact_report(report, RedactionConfig(redact_paths=True, salt="s"))

    assert redacted.coverage_note() == "coverage matched 0 of 2 scanned files"


def test_a_file_with_no_function_is_not_counted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty `__init__.py` is absent from most reports and has nothing to cover."""
    _project(tmp_path, monkeypatch, _ELSEWHERE)
    (tmp_path / "src" / "__init__.py").write_text("", encoding="utf-8")

    _, out, _ = _run("scan", "src")

    assert _NOTE in " ".join(out.split())
