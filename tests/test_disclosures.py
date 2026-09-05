"""0.3.6: the gate must say what it did not check.

Reproduced on `v0.3.5`: three of four baseline entries hidden by an `exclude` produced
"No risk regressions detected.", no stderr, exit 0 — only the PR comment's collapsed diff
said `Removed: 3`. A rename whose body also changed (`risky` 49.75 → `risky_v2` 52.25 under
`--fail-regression-above 1`) exited 0 with no signal. `_emit_regression_hint` printed the
raw baseline path under `--private-comment` while stdout was hashed. `explain --summary
--json` dropped `language`; `check --json` regressions had no `group`. And a scan path
outside the config directory kept whatever spelling it was passed, so the same file had one
key per cwd and its SARIF `uri` disagreed with its own `properties.path`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from textwrap import dedent

import pytest
from click.testing import Result
from typer.testing import CliRunner

from riskratchet.cli import app

runner = CliRunner()

_TWO_FUNCTIONS = "def alpha(a):\n    if a:\n        return 1\n    return 2\n\n\ndef beta(b):\n    return b\n"
_ONE_FUNCTION = "def gamma(c):\n    return c\n"
_RISKY = dedent(
    """
    def risky(a, b, c, d, e, f, g, h, i, j):
        if a: return 1
        if b: return 2
        if c: return 3
        if d: return 4
        if e: return 5
        if f: return 6
        if g: return 7
        if h: return 8
        if i: return 9
        if j: return 10
        return 0
    """
).strip()


def _project(tmp_path: Path, config: str = '[tool.riskratchet]\npaths = ["src"]\n') -> Path:
    """`src/a.py` (two functions) and `src/b.py` (one), baselined: three entries."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text(_TWO_FUNCTIONS, encoding="utf-8")
    (src / "b.py").write_text(_ONE_FUNCTION, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(config, encoding="utf-8")
    result = _run("baseline", "src")
    assert result.exit_code == 0, result.output
    return src


def _run(*args: str) -> Result:
    return runner.invoke(app, [*args, "--allow-missing-coverage", "--no-auto-cov", "--no-git"])


# --- 4.1 / 4.2: how much of the baseline was compared, and why some of it was not -------


def test_check_says_how_much_of_the_baseline_it_compared_in_every_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)

    table = _run("check", "src", "--exclude", "src/b.py")
    assert table.exit_code == 0, table.output
    assert "Baseline: 3 entries · 2 compared · 1 not seen this run" in table.stdout
    assert "No risk regressions detected." in table.stdout

    payload = json.loads(_run("check", "src", "--exclude", "src/b.py", "--json").stdout)
    assert payload["baseline"] == {"entries": 3, "compared": 2, "removed": 1}

    markdown = _run("check", "src", "--exclude", "src/b.py", "--format", "markdown").stdout
    assert "_Baseline: 3 entries · 2 compared · 1 not seen this run_" in markdown

    comment = _run("check", "src", "--exclude", "src/b.py", "--format", "pr-comment").stdout
    assert "_Baseline: 3 entries · 2 compared · 1 not seen this run_" in comment

    summary = _run("check", "src", "--exclude", "src/b.py", "--summary").stdout
    assert "baseline entries=3 compared=2 removed=1" in summary
    summary_json = json.loads(_run("check", "src", "--exclude", "src/b.py", "--summary", "--json").stdout)
    assert summary_json["summary"]["baseline"] == {"entries": 3, "compared": 2, "removed": 1}


def test_no_baseline_mode_carries_no_baseline_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)
    (tmp_path / ".riskratchet.json").unlink()

    result = _run("check", "src", "--fail-above", "99")

    assert result.exit_code == 0, result.output
    assert "Baseline:" not in result.stdout
    assert "baseline" not in json.loads(_run("check", "src", "--fail-above", "99", "--json").stdout)


def test_an_exclude_that_hides_a_baselined_file_warns_with_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)

    result = _run("check", "src", "--exclude", "src/b.py")

    assert result.exit_code == 0, result.output  # warn, never fail
    assert (
        "1 baseline entry lives in 1 file that was under the scanned paths but not scanned" in result.stderr
    )
    assert "include / exclude?" in result.stderr
    diff = _run("diff", "src", "--exclude", "src/b.py")
    assert "not scanned this run" in diff.stderr


def test_a_deliberate_subset_does_not_warn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`check src/a.py` against a repo baseline: `b.py` is outside the scanned root, so
    the disclosure line still counts it as not seen, but nothing suspects a filter."""
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)

    result = _run("check", "src/a.py")

    assert result.exit_code == 0, result.output
    assert "Baseline: 3 entries · 2 compared · 1 not seen this run" in result.stdout
    assert "not scanned this run" not in result.stderr


def test_deleting_the_file_does_not_warn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    (src / "b.py").unlink()

    result = _run("check", "src")

    assert result.exit_code == 0, result.output
    assert "1 not seen this run" in result.stdout
    assert "not scanned this run" not in result.stderr


def test_deleting_a_function_from_a_scanned_file_does_not_warn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file was reached (it is in `files[]` with what is left), so its missing
    function is a deletion, not a filter."""
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    (src / "b.py").write_text("X = 1\n", encoding="utf-8")

    result = _run("check", "src")

    assert result.exit_code == 0, result.output
    assert "1 not seen this run" in result.stdout
    assert "not scanned this run" not in result.stderr


def test_private_comment_still_warns_with_counts_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _project(tmp_path, '[tool.riskratchet]\npaths = ["src"]\nprivate_comment = true\nredact_salt = "s"\n')

    result = _run("check", "src", "--exclude", "src/b.py", "--format", "pr-comment")

    assert result.exit_code == 0, result.output
    warning = next(line for line in result.stderr.splitlines() if "not scanned this run" in line)
    assert "b.py" not in warning
    assert "_Baseline: 3 entries · 2 compared · 1 not seen this run_" in result.stdout


def test_a_symlinked_scan_root_keeps_its_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`src` -> `../real_src`: the key stays `src/a.py`, so nothing is "not seen" and
    a baseline written on 0.3.5 (whose fallback produced the same key) keeps matching."""
    real = tmp_path / "real_src"
    real.mkdir()
    (real / "a.py").write_text(_TWO_FUNCTIONS, encoding="utf-8")
    try:
        os.symlink(real, tmp_path / "src", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    (tmp_path / "pyproject.toml").write_text('[tool.riskratchet]\npaths = ["src"]\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert _run("baseline", "src").exit_code == 0
    baseline = json.loads((tmp_path / ".riskratchet.json").read_text(encoding="utf-8"))
    assert {entry["path"] for entry in baseline["entries"]} == {"src/a.py"}

    result = _run("check", "src")

    assert result.exit_code == 0, result.output
    assert "Baseline: 2 entries · 2 compared · 0 not seen this run" in result.stdout
    assert "not scanned this run" not in result.stderr
    assert "outside the config directory" not in result.stderr


# --- 4.3: rename + edit -------------------------------------------------------------------


def test_a_renamed_and_edited_function_is_gated_as_new_and_the_note_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    (src / "a.py").write_text(_RISKY + "\n", encoding="utf-8")
    assert _run("baseline", "src").exit_code == 0
    renamed = _RISKY.replace("def risky(", "def risky_v2(").replace("return 0", "return -1")
    (src / "a.py").write_text(renamed + "\n", encoding="utf-8")

    result = _run("check", "src", "--fail-regression-above", "1", "--fail-new-above", "99")

    assert result.exit_code == 0, result.output  # the contract: gated as new, under 99
    assert "1 function left the baseline and 1 appeared" in result.stderr
    assert "gated as new (fail_new_above=99)" in result.stderr
    assert "riskratchet diff" in result.stderr


def test_the_rename_note_is_silent_without_both_sides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    (src / "c.py").write_text("def delta(d):\n    return d\n", encoding="utf-8")

    result = _run("check", "src")

    assert result.exit_code == 0, result.output
    assert "left the baseline" not in result.stderr


# --- 4.4 / 4.5 / 4.6: the small ones ------------------------------------------------------


def test_the_regression_hint_hides_the_baseline_path_under_private_comment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    secret = tmp_path / "secret-dir"
    secret.mkdir()
    (tmp_path / ".riskratchet.json").rename(secret / ".riskratchet.json")
    (src / "a.py").write_text(_RISKY + "\n", encoding="utf-8")

    result = _run("check", "src", "--baseline", "secret-dir/.riskratchet.json", "--private-comment")

    assert result.exit_code == 1, result.output
    assert "secret-dir" not in result.stdout + result.stderr
    assert "--output <baseline.json>" in result.stderr
    plain = _run("check", "src", "--baseline", "secret-dir/.riskratchet.json")
    assert "secret-dir/.riskratchet.json" in plain.stderr


def test_explain_summary_carries_the_language(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)

    result = runner.invoke(
        app, ["explain", "src/a.py::alpha", "--summary", "--json", "--no-auto-cov", "--no-git"]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["summary"]["language"] == "python"


def test_check_json_regressions_carry_their_group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(
        tmp_path, '[tool.riskratchet]\npaths = ["src"]\n\n[tool.riskratchet.groups]\napi = ["src"]\n'
    )
    (src / "a.py").write_text(_RISKY + "\n", encoding="utf-8")

    result = _run("check", "src", "--json")

    assert result.exit_code == 1, result.output
    regressions = json.loads(result.stdout)["regressions"]
    assert regressions and all(reg["group"] == "api" for reg in regressions)


# --- 4.7: one spelling per file, and the SARIF uri is the key -----------------------------


def _out_of_root(tmp_path: Path) -> tuple[Path, Path]:
    proj = tmp_path / "proj"
    (proj / "sub").mkdir(parents=True)
    (proj / "pyproject.toml").write_text('[tool.riskratchet]\npaths = ["src"]\n', encoding="utf-8")
    (proj / "src").mkdir()
    (proj / "src" / "own.py").write_text(_ONE_FUNCTION, encoding="utf-8")
    other = tmp_path / "other" / "src"
    other.mkdir(parents=True)
    (other / "app.py").write_text(_TWO_FUNCTIONS, encoding="utf-8")
    return proj, other


def _scanned_paths(result: Result) -> set[str]:
    return {fn["path"] for fn in json.loads(result.stdout)["functions"]}


def test_an_out_of_root_file_has_one_key_from_every_cwd_and_spelling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proj, other = _out_of_root(tmp_path)

    monkeypatch.chdir(proj)
    from_root_relative = runner.invoke(app, ["scan", "../other/src", "--json", "--no-auto-cov", "--no-git"])
    from_root_absolute = runner.invoke(app, ["scan", str(other), "--json", "--no-auto-cov", "--no-git"])
    monkeypatch.chdir(proj / "sub")
    from_sub = runner.invoke(app, ["scan", "../../other/src", "--json", "--no-auto-cov", "--no-git"])

    for result in (from_root_relative, from_root_absolute, from_sub):
        assert result.exit_code == 0, result.output
        assert _scanned_paths(result) == {"../other/src/app.py"}
        assert "outside the config directory" in result.stderr


def test_a_scan_path_inside_the_root_does_not_warn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proj, _ = _out_of_root(tmp_path)
    monkeypatch.chdir(proj)

    result = runner.invoke(app, ["scan", "src", "--json", "--no-auto-cov", "--no-git"])

    assert result.exit_code == 0, result.output
    assert _scanned_paths(result) == {"src/own.py"}
    assert "outside the config directory" not in result.stderr


def test_sarif_uri_is_the_functions_own_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proj, _ = _out_of_root(tmp_path)
    monkeypatch.chdir(proj / "sub")

    result = runner.invoke(
        app, ["scan", "../../other/src", "../src", "--format", "sarif", "--no-auto-cov", "--no-git"]
    )

    assert result.exit_code == 0, result.output
    results = json.loads(result.stdout)["runs"][0]["results"]
    assert results
    for entry in results:
        uri = entry["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        assert uri == entry["properties"]["path"]
    assert {entry["properties"]["path"] for entry in results} == {"../other/src/app.py", "src/own.py"}


def test_explain_resolves_an_absolute_out_of_root_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.3.5 keyed an out-of-root file by its absolute spelling, so `explain /abs/x.py::f`
    worked by accident; it keeps working, by the key the report uses."""
    proj, other = _out_of_root(tmp_path)
    monkeypatch.chdir(proj)

    result = runner.invoke(app, ["explain", f"{other / 'app.py'}::alpha", "--no-auto-cov", "--no-git"])

    assert result.exit_code == 0, result.output
    assert "alpha" in result.stdout
