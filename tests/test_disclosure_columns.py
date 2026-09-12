"""A per-row table discloses the group and the language it has (0.3.7).

Before this release the group column existed in exactly one of six markdown/PR-comment
tables — the report PR comment — where it printed a column of "ungrouped" whether or not
`[tool.riskratchet.groups]` had placed anything, and the three Rich tables had no column
at all. A mixed Python/TypeScript repo had no way to tell which `handler::run` a row was
about either.

One rule replaces both halves: a column appears when it carries information. So an
ungrouped Python-only repo's output is byte-identical to 0.3.6 — this release moves no
verdicts and adds no noise to the common path — and a repo that configured groups, or
scans TypeScript, gets the column in every table rather than one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from riskratchet.cli import app

runner = CliRunner()

_PY = """\
def widget(flag):
    if flag:
        return 1
    return 0
"""
_TS = """\
export function gadget(flag: boolean): number {
  if (flag) { return 1; }
  return 0;
}
"""


def _project(tmp_path: Path, *, groups: bool = False, typescript: bool = False) -> Path:
    (tmp_path / "src").mkdir(parents=True)
    (tmp_path / "src" / "m.py").write_text(_PY, encoding="utf-8")
    if typescript:
        (tmp_path / "src" / "m.ts").write_text(_TS, encoding="utf-8")
    extra = "allow_missing_coverage = true\n"
    if typescript:
        extra += "typescript = true\n"
    if groups:
        extra += '\n[tool.riskratchet.groups]\ncore = ["src/"]\n'
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0"\n\n[tool.riskratchet]\npaths = ["src"]\n' + extra,
        encoding="utf-8",
    )
    return tmp_path


def _run(project: Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> str:
    monkeypatch.chdir(project)
    result = runner.invoke(app, [*args, "--no-auto-cov", "--no-git"])
    assert result.exit_code in (0, 1), (result.stdout, result.stderr)
    return result.stdout


@pytest.mark.parametrize("fmt", ["markdown", "pr-comment"])
def test_an_ungrouped_python_repo_gets_no_group_and_no_language_column(
    fmt: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _run(_project(tmp_path), monkeypatch, "scan", "--format", fmt)
    assert "| Group |" not in out
    assert "ungrouped" not in out
    assert "| Language |" not in out


@pytest.mark.parametrize("fmt", ["markdown", "pr-comment"])
def test_a_grouped_repo_gets_the_group_column(
    fmt: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _run(_project(tmp_path, groups=True), monkeypatch, "scan", "--format", fmt)
    assert "| Group |" in out
    assert "| core |" in out


@pytest.mark.parametrize("fmt", ["markdown", "pr-comment"])
def test_a_typescript_repo_gets_the_language_column(
    fmt: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    out = _run(_project(tmp_path, typescript=True), monkeypatch, "scan", "--format", fmt)
    assert "| Language |" in out
    assert "| typescript |" in out
    assert "| python |" in out


def test_the_regressions_and_diff_tables_carry_the_columns_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate's own output, not just `scan` — the tables a reviewer actually reads."""
    project = _project(tmp_path, groups=True)
    monkeypatch.chdir(project)
    assert runner.invoke(app, ["baseline", "--no-auto-cov", "--no-git"]).exit_code == 0
    grown = "def widget(flag):\n" + "".join(f"    if flag == {n}:\n        return {n}\n" for n in range(12))
    (project / "src" / "m.py").write_text(grown + "    return 0\n", encoding="utf-8")
    for args in (
        ["check", "--format", "markdown"],
        ["check", "--format", "pr-comment"],
        ["diff", "--format", "markdown"],
        ["diff", "--format", "pr-comment"],
    ):
        out = _run(project, monkeypatch, *args)
        assert "| Group |" in out, args
        assert "| core |" in out, args


def test_the_terminal_tables_show_the_group_only_when_one_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Rich table spends its 80 columns on a `no_wrap` Function cell already.

    Unlike markdown, where width is free, a column of "ungrouped" here would squeeze the
    one cell the reader came for — so the terminal tables stay conditional even though
    the markdown ones could afford to be unconditional.
    """
    plain = _run(_project(tmp_path), monkeypatch, "scan")
    assert "Group" not in plain

    project = _project(tmp_path / "grouped", groups=True)
    grouped = _run(project, monkeypatch, "scan")
    assert "Group" in grouped
    assert "core" in grouped

    monkeypatch.chdir(project)
    assert runner.invoke(app, ["baseline", "--no-auto-cov", "--no-git"]).exit_code == 0
    (project / "src" / "m.py").write_text(_PY.replace("return 0", "return 2"), encoding="utf-8")
    assert "Group" in _run(project, monkeypatch, "diff")


def test_a_removed_typescript_function_is_still_marked_as_typescript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A removed row has no `current`, so the language has to come off the baseline entry.

    Reading it off the live side alone would report every removed TypeScript function as
    Python — the one row where the marker matters most, because the source is gone.
    """
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    project = _project(tmp_path, typescript=True)
    monkeypatch.chdir(project)
    assert runner.invoke(app, ["baseline", "--no-auto-cov", "--no-git"]).exit_code == 0
    (project / "src" / "m.ts").unlink()
    out = _run(project, monkeypatch, "diff", "--format", "markdown")
    removed = [line for line in out.splitlines() if line.startswith("| removed |")]
    assert removed and all("| typescript |" in line for line in removed), out


def test_the_json_document_is_unchanged_by_any_of_this(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The columns are a rendering concern; `group` and `language` were already in JSON."""
    payload = json.loads(_run(_project(tmp_path, groups=True), monkeypatch, "scan", "--json"))
    assert payload["functions"][0]["group"] == "core"
    assert payload["functions"][0]["language"] == "python"
