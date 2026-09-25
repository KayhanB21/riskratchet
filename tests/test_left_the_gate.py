"""0.3.9: a baselined function that leaves the gate says why.

Reproduced on `v0.3.8`: a repository baselines four functions, and one PR makes three of them
riskier while taking each out of the gate a different way — an `allow` pattern, a `# @generated`
header, and a syntax error. `check` exited 0 with "No risk regressions detected.", and the PR
comment filed all three under **Removed functions (3)** with the reason "removed function from
baseline", although all three were still in the code. Only the parse error reached stderr.

The 0.3.7 `exclude` guard (`unscanned_baseline_files`) could not see any of it: it treats an
entry as deleted whenever its file is in `report.files`, and all of these exits keep the file
there. The verdict stays the same (warn, never fail); what changes is that the tool says so.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import Result
from typer.testing import CliRunner

from riskratchet.cli import app

runner = CliRunner()

_SIMPLE = "def {name}(x):\n    return x\n"
_BRANCHY = (
    "def {name}(x):\n"
    "    if x == 1:\n        return 1\n"
    "    if x == 2:\n        return 2\n"
    "    if x == 3:\n        return 3\n"
    "    if x == 4:\n        return 4\n"
    "    if x == 5:\n        return 5\n"
    "    return 0\n"
)
_CONFIG = '[tool.riskratchet]\npaths = ["src"]\n'
_REMOVED_REASON = "removed function from baseline"


def _flat(text: str) -> str:
    """Rich wraps the table door at the test runner's 80 columns."""
    return " ".join(text.split())


def _run(*args: str) -> Result:
    return runner.invoke(app, [*args, "--allow-missing-coverage", "--no-auto-cov", "--no-git"])


def _baselined(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: str = _CONFIG) -> Path:
    """`src/{a,b,c,d}.py`, one simple function each, baselined: four entries."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    for module, name in (("a", "f"), ("b", "g"), ("c", "h"), ("d", "k")):
        (src / f"{module}.py").write_text(_SIMPLE.format(name=name), encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(config, encoding="utf-8")
    result = _run("baseline", "src")
    assert result.exit_code == 0, result.output
    return src


def _three_exits(src: Path, tmp_path: Path, config: str = _CONFIG) -> None:
    """Make `f`, `g`, and `h` riskier and take each out of the gate a different way."""
    (src / "a.py").write_text(_BRANCHY.format(name="f"), encoding="utf-8")
    (src / "b.py").write_text("# @generated\n" + _BRANCHY.format(name="g"), encoding="utf-8")
    (src / "c.py").write_text("def h(:\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(config + 'allow = ["src/a.py::f"]\n', encoding="utf-8")


def test_the_three_exits_are_named_in_every_check_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _baselined(tmp_path, monkeypatch)
    _three_exits(src, tmp_path)

    table = _run("check", "src")
    assert table.exit_code == 0, table.output  # warn, never fail: the verdict is unchanged
    breakdown = "(1 suppressed by allow, 1 in a @generated file, 1 failed to parse)"
    assert f"Baseline: 4 entries · 1 compared · 3 not seen this run {breakdown}" in _flat(table.stdout)
    assert (
        "warning: 3 baseline entries are still in the code but were not scored this run "
        f"{breakdown}" in _flat(table.stderr)
    )

    comment = _run("check", "src", "--format", "pr-comment").stdout
    assert f"_Baseline: 4 entries · 1 compared · 3 not seen this run {breakdown}_" in comment
    assert _REMOVED_REASON not in comment
    assert "suppressed by an allow pattern this run (was " in comment
    assert "its file carries a @generated marker this run (was " in comment
    assert "its file failed to parse this run (was " in comment

    markdown = _run("check", "src", "--format", "markdown").stdout
    assert f"3 not seen this run {breakdown}" in markdown


def test_the_json_payload_does_not_change_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`regressions.schema.json` sets `additionalProperties: false` on `baseline`: the cause
    travels in text, never in a new field."""
    src = _baselined(tmp_path, monkeypatch)
    _three_exits(src, tmp_path)

    payload = json.loads(_run("check", "src", "--json").stdout)

    assert payload["baseline"] == {"entries": 4, "compared": 1, "removed": 3}


def test_the_diff_command_names_the_cause(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _baselined(tmp_path, monkeypatch)
    _three_exits(src, tmp_path)

    result = _run("diff", "src", "--json")

    reasons = {e["qualname"]: e["reason"] for e in json.loads(result.stdout)["entries"]}
    assert reasons["f"].startswith("suppressed by an allow pattern this run")
    assert reasons["g"].startswith("its file carries a @generated marker this run")
    assert reasons["h"].startswith("its file failed to parse this run")
    assert "not scored this run" in result.stderr


@pytest.mark.parametrize(
    ("edit", "label"),
    [
        ("allow", "1 suppressed by allow"),
        ("generated", "1 in a @generated file"),
        ("parse", "1 failed to parse"),
    ],
)
def test_each_exit_on_its_own(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, edit: str, label: str) -> None:
    src = _baselined(tmp_path, monkeypatch)
    if edit == "allow":
        (tmp_path / "pyproject.toml").write_text(_CONFIG + 'allow = ["src/a.py::f"]\n', encoding="utf-8")
    elif edit == "generated":
        (src / "b.py").write_text("# @generated\n" + _SIMPLE.format(name="g"), encoding="utf-8")
    else:
        (src / "c.py").write_text("def h(:\n", encoding="utf-8")

    result = _run("check", "src")

    assert result.exit_code == 0, result.output
    assert f"1 not seen this run ({label})" in _flat(result.stdout)
    assert f"1 baseline entry is still in the code but was not scored this run ({label})" in _flat(
        result.stderr
    )


def test_missing_coverage_skip_is_a_fourth_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`missing_coverage = "skip"` drops every function of a file absent from the report."""
    _baselined(tmp_path, monkeypatch)
    covered = {"executed_lines": [1, 2], "missing_lines": [], "executed_branches": [], "missing_branches": []}
    (tmp_path / "coverage.json").write_text(
        json.dumps({"files": {f"src/{m}.py": covered for m in ("a", "c", "d")}}), encoding="utf-8"
    )

    result = _run("check", "src", "--coverage", "coverage.json", "--missing-coverage", "skip")

    assert result.exit_code == 0, result.output
    assert "1 not seen this run (1 skipped for missing coverage)" in _flat(result.stdout)
    diff = json.loads(
        _run("diff", "src", "--coverage", "coverage.json", "--missing-coverage", "skip", "--json").stdout
    )
    reasons = {e["qualname"]: e["reason"] for e in diff["entries"]}
    assert reasons["g"].startswith("its file has no coverage entry and missing_coverage is skip this run")


def test_a_deletion_keeps_its_reason_and_says_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = _baselined(tmp_path, monkeypatch)
    (src / "b.py").unlink()
    (src / "c.py").write_text("X = 1\n", encoding="utf-8")

    result = _run("check", "src", "--format", "pr-comment")

    assert result.exit_code == 0, result.output
    assert "_Baseline: 4 entries · 2 compared · 2 not seen this run_" in result.stdout
    assert result.stdout.count(_REMOVED_REASON) == 2
    assert "not scored this run" not in result.stderr


def test_private_comment_names_no_path_or_pattern(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _CONFIG + 'private_comment = true\nredact_salt = "s"\n'
    src = _baselined(tmp_path, monkeypatch, config)
    _three_exits(src, tmp_path, config)

    result = _run("check", "src", "--format", "pr-comment")

    assert result.exit_code == 0, result.output
    assert "3 not seen this run (1 suppressed by allow" in result.stdout
    warning = next(line for line in result.stderr.splitlines() if "not scored this run" in line)
    for leak in ("a.py", "b.py", "c.py", "src/"):
        assert leak not in warning
        assert leak not in result.stdout


def test_the_rename_note_ignores_entries_that_are_still_in_the_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A suppressed function plus an unrelated new one is not a rename."""
    src = _baselined(tmp_path, monkeypatch)
    (tmp_path / "pyproject.toml").write_text(_CONFIG + 'allow = ["src/a.py::f"]\n', encoding="utf-8")
    (src / "e.py").write_text("def brand_new(y):\n    return [y, y]\n", encoding="utf-8")

    result = _run("check", "src")

    assert result.exit_code == 0, result.output
    assert "left the baseline and" not in result.stderr


def test_a_generated_typescript_file_names_its_cause(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter")
    from riskratchet import typescript_engine as te
    from riskratchet.baseline import baseline_from_report, diff

    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    gen = root / "src" / "gen.ts"
    gen.write_text("export function made(a: number): number { return a; }\n", encoding="utf-8")
    old = baseline_from_report(te.analyze_typescript([root / "src"], root=root, use_git=False))
    gen.write_text("// @generated by some-tool\n" + gen.read_text(encoding="utf-8"), encoding="utf-8")

    report = te.analyze_typescript([root / "src"], root=root, use_git=False)
    entry = next(iter(diff(report, old, fail_regression_above=5.0).entries))

    assert entry.reason.startswith("its file carries a @generated marker this run")
