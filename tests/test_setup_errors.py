"""Tests for P26: actionable setup errors.

Each test exercises one of the top first-failure sites and asserts that the
remediation command appears in stderr. The shape we contract on:

  riskratchet: <headline>

  Fix one of:
    1. <description>
         <command>

so the existence of a concrete copy-pasteable command is the load-bearing
invariant — not the exact wording of the headline.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from textwrap import dedent

import pytest
from click.testing import Result
from typer.testing import CliRunner

from riskratchet import auto_coverage
from riskratchet.auto_coverage import _default_runner as _real_default_runner
from riskratchet.cli import app

runner = CliRunner()


def _project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text(
        dedent(
            """
            def trivial():
                return 1
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    return tmp_path / "src"


def test_missing_coverage_emits_pytest_remediation_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    # No coverage anywhere, no --allow-missing-coverage: hard fail.
    result = runner.invoke(app, ["baseline", str(src), "--no-auto-cov", "--no-git"])
    assert result.exit_code == 2, result.output
    assert "Fix one of:" in result.stderr
    assert "pytest --cov" in result.stderr
    assert "--allow-missing-coverage" in result.stderr
    assert "--no-auto-cov" in result.stderr


def test_missing_baseline_emits_baseline_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    result = runner.invoke(
        app,
        ["check", str(src), "--allow-missing-coverage", "--no-auto-cov", "--no-git"],
    )
    assert result.exit_code == 2, result.output
    assert "baseline file not found" in result.stderr
    assert "Fix one of:" in result.stderr
    assert "riskratchet baseline" in result.stderr
    # P28 fallback is mentioned as a remediation:
    assert "--fail-above" in result.stderr


def test_missing_baseline_in_diff_emits_baseline_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    result = runner.invoke(
        app,
        ["diff", str(src), "--allow-missing-coverage", "--no-auto-cov", "--no-git"],
    )
    assert result.exit_code == 2, result.output
    assert "baseline file not found" in result.stderr
    assert "riskratchet baseline" in result.stderr


def test_malformed_baseline_emits_regenerate_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    # Junk-bytes baseline so json.loads raises and triggers the new helper.
    baseline = tmp_path / "bad.json"
    baseline.write_text("{not valid json", encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "check",
            str(src),
            "--baseline",
            str(baseline),
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert result.exit_code == 2, result.output
    assert "cannot read baseline" in result.stderr
    assert "Fix one of:" in result.stderr
    assert "riskratchet baseline" in result.stderr


# Each payload is a baseline that riskratchet cannot honestly ratchet against.
# Before 0.3.3 every one of them loaded as *zero entries* and reported a clean
# run — a corrupt `.riskratchet.json` silently switched the gate off. The
# non-dict root was worse still: a raw `AttributeError` traceback and exit 1.
_UNUSABLE_BASELINES = {
    "non_dict_root": ("[1, 2, 3]", "must be a JSON object"),
    "entries_not_a_list": ('{"version": "3", "entries": "nope"}', "'entries'"),
    "entries_missing": ('{"version": "3"}', "'entries'"),
    "version_from_the_future": ('{"version": "99", "entries": []}', "newer riskratchet"),
    "version_unrecognized": ('{"version": "nope", "entries": []}', "unrecognized baseline version"),
}


@pytest.mark.parametrize("command", ("check", "diff"))
@pytest.mark.parametrize(
    ("payload", "expected"),
    list(_UNUSABLE_BASELINES.values()),
    ids=list(_UNUSABLE_BASELINES),
)
def test_unusable_baseline_exits_two_instead_of_passing_silently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    payload: str,
    expected: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    baseline = tmp_path / "bad.json"
    baseline.write_text(payload, encoding="utf-8")

    result = runner.invoke(
        app,
        [
            command,
            str(src),
            "--baseline",
            str(baseline),
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )

    assert result.exit_code == 2, result.output
    assert expected in result.stderr
    assert "riskratchet baseline" in result.stderr
    # The non-dict root used to reach the user as an unhandled AttributeError.
    assert "Traceback" not in result.stderr


def test_future_baseline_offers_upgrade_before_regenerate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one baseline error where "regenerate" is the wrong first answer.

    A baseline from a newer riskratchet is still a good file; regenerating would
    downgrade it for no reason. Upgrading must be offered first.
    """
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    baseline = tmp_path / "future.json"
    baseline.write_text('{"version": "99", "entries": []}', encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "check",
            str(src),
            "--baseline",
            str(baseline),
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )

    assert result.exit_code == 2, result.output
    stderr = result.stderr
    assert "pip install --upgrade riskratchet" in stderr
    assert stderr.index("pip install --upgrade") < stderr.index("riskratchet baseline")


def test_malformed_entries_warn_but_do_not_stop_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dropped entry leaves that function unratcheted — say so, don't exit."""
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    baseline = tmp_path / "partial.json"
    # The surviving entry is scored generously *in every component* so nothing
    # regresses and the exit code isolates the one thing under test: a dropped entry
    # is not fatal. An empty `components` map would leave every component at 0 and
    # trip the component gate, which since 0.3.5 runs even when the total improved.
    generous = (
        '{"coverage_gap": 100.0, "structural_complexity": 100.0, "branch_gap": 100.0, '
        '"churn": 100.0, "public_surface": 100.0, "sprawl": 100.0}'
    )
    baseline.write_text(
        '{"version": "3", "entries": ['
        '{"path": "src/m.py", "qualname": "trivial", "score": 100.0, "components": ' + generous + "},"
        '{"path": "src/m.py", "qualname": "broken", "score": "not-a-number", "components": {}}'
        "]}",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "check",
            str(src),
            "--baseline",
            str(baseline),
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "skipped 1 malformed entry" in result.stderr
    assert "not ratcheted" in result.stderr


def test_missing_scan_path_arg_emits_remediation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    # Path that does not exist on disk — today's behaviour is a silent empty
    # report; P26 fails fast with an actionable error.
    result = runner.invoke(
        app,
        ["scan", "src/typo.py", "--no-auto-cov", "--no-git"],
    )
    assert result.exit_code == 2, result.output
    assert "src/typo.py" in result.stderr
    assert "Fix one of:" in result.stderr
    # Remediation hint is "check spelling, list a different path":
    assert "Check the path spelling" in result.stderr


def test_missing_scan_path_in_config_emits_remediation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    # No src/ dir; config points at non-existent path.
    (tmp_path / "pyproject.toml").write_text(
        dedent(
            """
            [tool.riskratchet]
            paths = ["nonexistent_pkg"]
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["scan", "--no-auto-cov", "--no-git"],
    )
    assert result.exit_code == 2, result.output
    assert "nonexistent_pkg" in result.stderr
    assert "Edit pyproject.toml" in result.stderr


def test_stale_coverage_test_command_failure_emits_remediation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: `riskratchet baseline` with auto-coverage enabled, but the
    test command produces no coverage.json, surfaces the new remediation
    block on stderr. Overrides the autouse `refuse` stub for this one
    test so the auto-coverage path actually runs.
    """
    import riskratchet.auto_coverage as auto_coverage

    def fake_runner_that_writes_nothing(command: str, cwd: Path) -> int:
        # Match the real runner shape (str, Path) but skip the side effect
        # — the test asserts that an empty result triggers the hint.
        return 0

    monkeypatch.setattr(auto_coverage, "_default_runner", fake_runner_that_writes_nothing)
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    result = runner.invoke(
        app,
        ["baseline", str(src), "--no-git"],
    )
    # exit 2 because auto-coverage produced nothing and no fallback is allowed.
    assert result.exit_code == 2, (result.stdout, result.stderr)
    assert "test command did not produce" in result.stderr
    assert "Fix one of:" in result.stderr
    assert "pytest --cov" in result.stderr
    assert "--no-auto-cov" in result.stderr


def _seed_baseline(src: Path) -> None:
    """Write a real baseline so `check`/`diff` get past their baseline gate.

    Both commands resolve the baseline before touching coverage, so without
    this they exit 2 on "baseline file not found" and never reach the coverage
    boundary under test.
    """
    result = runner.invoke(
        app, ["baseline", str(src), "--allow-missing-coverage", "--no-auto-cov", "--no-git"]
    )
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("command", ["scan", "check", "diff", "baseline"])
def test_malformed_coverage_emits_setup_error(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A coverage file that won't parse is a setup problem, not a crash.

    Before 0.3.2 every command wrapped `build_report` in `except ImportError`
    only, so `load_coverage`'s `ValueError` escaped as a raw traceback. All
    four commands now share one boundary (`cli._build_report_or_exit`).
    """
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    _seed_baseline(src)
    bad = tmp_path / "coverage.json"
    bad.write_text("not json at all", encoding="utf-8")

    result = runner.invoke(app, [command, str(src), "--coverage", str(bad), "--no-auto-cov", "--no-git"])

    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.output
    assert "could not read coverage file" in result.stderr
    assert "Fix one of:" in result.stderr
    assert "pytest --cov" in result.stderr


@pytest.mark.parametrize("command", ["scan", "check", "diff", "baseline"])
def test_coverage_file_without_files_section_emits_setup_error(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valid JSON, wrong shape — the other `ValueError` branch in `load_coverage`."""
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    _seed_baseline(src)
    bad = tmp_path / "coverage.json"
    bad.write_text('{"meta": {}}', encoding="utf-8")

    result = runner.invoke(app, [command, str(src), "--coverage", str(bad), "--no-auto-cov", "--no-git"])

    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.output
    assert "has no `files` section" in result.stderr
    assert "Fix one of:" in result.stderr


def test_scan_with_missing_coverage_map_shard_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`scan` passes `allow_missing=True`, so an absent shard must warn, not crash."""
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)

    result = runner.invoke(
        app,
        ["scan", str(src), "--coverage-map", f"src={tmp_path / 'absent.json'}", "--no-auto-cov", "--no-git"],
    )

    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output
    assert "treating that prefix as no coverage" in result.stderr
    assert "pytest --cov" in result.stderr


def test_scan_with_malformed_coverage_map_shard_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    junk = tmp_path / "junk.json"
    junk.write_text("not json", encoding="utf-8")

    result = runner.invoke(
        app,
        ["scan", str(src), "--coverage-map", f"src={junk}", "--no-auto-cov", "--no-git"],
    )

    assert result.exit_code == 0, result.output
    assert "coverage-map shard unusable" in result.stderr
    assert "could not read" in result.stderr


def test_strict_missing_coverage_map_shard_still_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`check` does not allow missing coverage, so the strict path is unchanged."""
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    _seed_baseline(src)

    result = runner.invoke(
        app,
        ["check", str(src), "--coverage-map", f"src={tmp_path / 'absent.json'}", "--no-auto-cov", "--no-git"],
    )

    assert result.exit_code == 2, result.output
    assert "coverage-map[src] file not found" in result.stderr
    assert "--allow-missing-coverage" in result.stderr


def test_shallow_clone_emits_churn_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A shallow clone silently zeroes churn, so say so.

    This is the runtime half of the `fetch-depth: 0` fix: an adopter whose CI
    still uses a depth-1 checkout gets told why their scores disagree with the
    baseline instead of silently getting different numbers.
    """
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "shallow").write_text("deadbeef\n", encoding="utf-8")

    result = runner.invoke(app, ["scan", str(src), "--no-auto-cov"])

    assert result.exit_code == 0, result.output
    assert "shallow clone detected" in result.stderr
    assert "fetch-depth: 0" in result.stderr


def test_no_git_suppresses_shallow_clone_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "shallow").write_text("deadbeef\n", encoding="utf-8")

    result = runner.invoke(app, ["scan", str(src), "--no-auto-cov", "--no-git"])

    assert result.exit_code == 0, result.output
    assert "shallow clone detected" not in result.stderr


# --- 0.3.4: a known config key with a wrong-typed value ------------------------
#
# `fail_new_above = "1"` used to be dropped in silence and the *default* 50
# applied, so a repo that thought it had tightened its gate had not. `config
# validate` always caught it; the analysis commands never did.

_ANALYSIS_COMMANDS = ("scan", "check", "diff", "baseline", "explain")

_UNUSABLE_VALUES: dict[str, tuple[str, str]] = {
    "number": ('fail_new_above = "1"', "fail_new_above must be a number."),
    "bool": ('auto_coverage = "yes"', "auto_coverage must be a boolean."),
    "string": ("baseline = 3", "baseline must be a string."),
    "string_list": ('exclude = "tests/**"', "exclude must be a list of strings."),
    "int_range": ("churn_window_days = 0", "churn_window_days must be an integer >= 1."),
}


def _config_project(tmp_path: Path, body: str) -> Path:
    """Seed a working project, *then* break its config.

    Order matters: `_seed_baseline` runs the real `baseline` command, which is
    itself one of the commands now rejecting a bad config, so writing the bad
    value first would fail the fixture rather than the assertion.
    """
    src = _project(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[tool.riskratchet]\npaths = ["src"]\n', encoding="utf-8")
    _seed_baseline(src)
    with (tmp_path / "pyproject.toml").open("a", encoding="utf-8") as handle:
        handle.write(f"{body}\n")
    return src


def _invoke(command: str, src: Path) -> Result:
    """Run one analysis command with the flags it actually accepts.

    `scan` and `explain` have no `--allow-missing-coverage` (neither hard-fails
    without coverage), and `explain` takes a function target rather than a path.
    """
    if command == "explain":
        return runner.invoke(app, ["explain", f"{src.name}/m.py::trivial", "--no-auto-cov", "--no-git"])
    args = [command, str(src), "--no-auto-cov", "--no-git"]
    if command != "scan":
        args.append("--allow-missing-coverage")
    return runner.invoke(app, args)


@pytest.mark.parametrize("command", _ANALYSIS_COMMANDS)
@pytest.mark.parametrize("kind", sorted(_UNUSABLE_VALUES))
def test_unusable_config_value_exits_two_instead_of_being_dropped(
    command: str, kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    body, message = _UNUSABLE_VALUES[kind]
    src = _config_project(tmp_path, body)

    result = _invoke(command, src)

    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.output
    assert message in result.stderr
    assert "riskratchet config validate" in result.stderr


def test_every_unusable_value_is_reported_in_one_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixing a config with four typos must not take four runs."""
    monkeypatch.chdir(tmp_path)
    src = _config_project(tmp_path, "\n".join(body for body, _ in _UNUSABLE_VALUES.values()))

    result = _invoke("check", src)

    assert result.exit_code == 2, result.output
    for _, message in _UNUSABLE_VALUES.values():
        assert message in result.stderr


def test_unknown_key_still_only_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The deliberate asymmetry: an unknown key may come from a newer riskratchet.

    Refusing to run on one would make upgrading riskratchet the only way to
    downgrade it, so unknown keys warn and the run continues. A *known* key with
    a wrong-typed value has no such forward-compatibility story.
    """
    monkeypatch.chdir(tmp_path)
    src = _config_project(tmp_path, "fail_new_abvoe = 1")

    result = _invoke("check", src)

    assert result.exit_code == 0, result.output
    assert "ignoring unknown [tool.riskratchet] key(s): fail_new_abvoe" in result.stderr


def test_analysis_commands_reject_every_config_that_config_validate_rejects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parity gate: the two paths must not drift apart again.

    The bug was exactly this drift — `config validate` knew `fail_new_above =
    "1"` was unusable and `check` did not. Asserting the two agree on a corpus
    is what keeps a future key from being added to one path only.
    """
    for index, (body, _) in enumerate(_UNUSABLE_VALUES.values()):
        case = tmp_path / f"case{index}"
        case.mkdir()
        monkeypatch.chdir(case)
        src = _config_project(case, body)
        assert runner.invoke(app, ["config", "validate"]).exit_code == 2, f"{body}: validate accepted it"
        assert _invoke("check", src).exit_code == 2, f"{body}: check accepted it"


# --- 0.3.4: a scan that finds nothing must not pass the gate -------------------
#
# A *nonexistent* path was already exit 2 (`_check_paths_exist`). An existing one
# matching no functions was not: `check` reported "No risk regressions detected"
# and exited 0, so a typo'd `paths`, a src/->lib/ restructure, or an over-broad
# `exclude` switched the ratchet off and passed green forever.

_RISKY = (
    "def trivial(a, b, c, d, e):\n"
    "    if a > 1:\n"
    "        if b > 2:\n"
    "            if c > 3:\n"
    "                if d > 4:\n"
    "                    if e > 5:\n"
    "                        return a + b + c + d + e\n"
    "    for i in range(a):\n"
    "        if i % 2:\n"
    "            return i\n"
    "    return 0\n"
)


def _project_with_regression(tmp_path: Path) -> Path:
    """Seed a baseline, then make `src/m.py` genuinely riskier than it."""
    src = _project(tmp_path)
    _seed_baseline(src)
    (src / "m.py").write_text(_RISKY, encoding="utf-8")
    return src


def _check(src: Path, *extra: str) -> Result:
    return runner.invoke(
        app, ["check", str(src), "--allow-missing-coverage", "--no-auto-cov", "--no-git", *extra]
    )


def test_the_regression_fires_before_anything_hides_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control for the two tests below: without a filter, the gate does trip."""
    monkeypatch.chdir(tmp_path)
    assert _check(_project_with_regression(tmp_path)).exit_code == 1


@pytest.mark.parametrize(
    ("flags", "cause"),
    [
        (("--exclude", "**"), "no functions were found to analyze"),
        (("--allow", "*"), "all suppressed by an allow pattern"),
    ],
    ids=["excluded_away", "suppressed_away"],
)
def test_a_filter_that_hides_everything_cannot_hide_a_regression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flags: tuple[str, ...], cause: str
) -> None:
    """The bug in one assertion: exit 1 must not become exit 0.

    Both filters empty the report, and the two causes carry different
    remediations, so the message must name which one happened.
    """
    monkeypatch.chdir(tmp_path)
    src = _project_with_regression(tmp_path)

    result = _check(src, *flags)

    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.output
    assert cause in result.stderr
    assert "the ratchet would check nothing" in result.stderr


def test_scanning_an_empty_directory_does_not_disengage_the_ratchet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _project_with_regression(tmp_path)
    (tmp_path / "elsewhere").mkdir()

    result = _check(tmp_path / "elsewhere")

    assert result.exit_code == 2, result.output
    assert "baseline has 1 entry to protect" in result.stderr


def test_an_empty_baseline_has_nothing_to_protect_so_it_only_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero functions against zero entries is a legitimately empty project.

    A monorepo CI that sweeps a package with no source yet must not start
    failing, so the hard error is scoped to a baseline that has entries.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "empty").mkdir()
    assert (
        runner.invoke(
            app,
            ["baseline", str(tmp_path / "empty"), "--allow-missing-coverage", "--no-auto-cov", "--no-git"],
        ).exit_code
        == 0
    )

    result = _check(tmp_path / "empty")

    assert result.exit_code == 0, result.output
    assert "no functions were found to analyze" in result.stderr


@pytest.mark.parametrize("command", ["scan", "diff"])
def test_inspection_commands_warn_without_changing_their_exit_code(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`scan` and `diff` have no gate to protect; they report, they don't fail."""
    monkeypatch.chdir(tmp_path)
    _project_with_regression(tmp_path)
    (tmp_path / "elsewhere").mkdir()

    args = [command, str(tmp_path / "elsewhere"), "--no-auto-cov", "--no-git"]
    if command != "scan":  # `scan` has no --allow-missing-coverage; it never hard-fails without coverage
        args.append("--allow-missing-coverage")
    result = runner.invoke(app, args)

    assert result.exit_code == 0, result.output
    assert f"riskratchet: {command}: no functions were found to analyze." in result.stderr


def test_baseline_refuses_to_overwrite_a_populated_baseline_with_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The destructive twin: the same typo used to erase the ratchet outright."""
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)
    _seed_baseline(tmp_path / "src")
    baseline = tmp_path / ".riskratchet.json"
    before = baseline.read_bytes()
    (tmp_path / "elsewhere").mkdir()

    result = runner.invoke(
        app,
        [
            "baseline",
            str(tmp_path / "elsewhere"),
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )

    assert result.exit_code == 2, result.output
    assert "refusing to overwrite" in result.stderr
    assert "discard its 1 entry" in result.stderr
    assert baseline.read_bytes() == before, "the baseline was modified despite the refusal"


def test_writing_a_fresh_zero_function_baseline_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only *erasing* entries is refused; a new empty baseline is legitimate."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "empty").mkdir()

    result = runner.invoke(
        app, ["baseline", str(tmp_path / "empty"), "--allow-missing-coverage", "--no-auto-cov", "--no-git"]
    )

    assert result.exit_code == 0, result.output
    assert (tmp_path / ".riskratchet.json").exists()


# --- 0.3.5: a coverage file the user named must exist -------------------------------
#
# `doctor` reported FAIL on a setup `check` ran happily: a named coverage path that
# did not exist fell through to auto-coverage, which generated a *different* file and
# gated against that. One typo turned a real exit-1 regression into exit 0, and
# `baseline` anchored the ratchet to coverage nobody asked for.


def _invoke_with_coverage(command: str, src: Path, coverage: str) -> Result:
    """`_invoke`, plus an explicit `--coverage` path, minus `--no-auto-cov`.

    Dropping `--no-auto-cov` is the point: with auto-coverage *on* — the default —
    the fallback used to succeed and hide the bad path entirely. `--allow-missing-coverage`
    is omitted too; it is the deliberate downgrade for this check, pinned separately.
    """
    if command == "explain":
        return runner.invoke(
            app, ["explain", f"{src.name}/m.py::trivial", "--no-git", "--coverage", coverage]
        )
    return runner.invoke(app, [command, str(src), "--no-git", "--coverage", coverage])


@pytest.mark.parametrize("command", _ANALYSIS_COMMANDS)
def test_a_coverage_file_named_on_the_command_line_must_exist(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[tool.riskratchet]\npaths = ["src"]\n', encoding="utf-8")
    _seed_baseline(src)

    result = _invoke_with_coverage(command, src, "absent-coverage.json")

    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.output
    assert "coverage file not found: absent-coverage.json" in result.stderr
    assert "Fix one of:" in result.stderr
    assert "pytest --cov" in result.stderr


def test_a_missing_coverage_path_cannot_hide_a_regression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug in two assertions: the same `check`, one character apart.

    Coverage is the largest single input to the score, so substituting a different
    file does not merely lose information — it changes every number the gate compares.
    """
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[tool.riskratchet]\npaths = ["src"]\n', encoding="utf-8")
    coverage = tmp_path / "coverage.json"
    coverage.write_text(
        '{"files": {"src/m.py": {"executed_lines": [], "missing_lines": [1, 2]}}}', encoding="utf-8"
    )
    seed = runner.invoke(
        app, ["baseline", str(src), "--coverage", str(coverage), "--no-auto-cov", "--no-git"]
    )
    assert seed.exit_code == 0, seed.output
    (src / "m.py").write_text(
        "def trivial():\n"
        + "".join(f"    if {i}:\n        return {i}\n" for i in range(12))
        + "    return 1\n",
        encoding="utf-8",
    )

    fired = runner.invoke(app, ["check", str(src), "--coverage", str(coverage), "--no-auto-cov", "--no-git"])
    typo = runner.invoke(app, ["check", str(src), "--coverage", f"{coverage}x", "--no-git"])

    assert fired.exit_code == 1, fired.output  # control: the gate does fire
    assert typo.exit_code == 2, typo.output  # and a typo cannot turn that into 0
    assert "No risk regressions detected" not in typo.output


def test_a_configured_coverage_path_warns_and_names_its_substitute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A config path is a default auto-coverage may fill, so it continues — but says so.

    Silence is what made this dangerous; the warning has to name the file actually
    used, or "continuing" is indistinguishable from "using what you asked for".
    """
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.riskratchet]\npaths = ["src"]\ncoverage = "ci-coverage.json"\nauto_coverage = false\n',
        encoding="utf-8",
    )

    result = runner.invoke(app, ["scan", str(src), "--no-git"])

    assert result.exit_code == 0, result.output
    assert "coverage file not found: ci-coverage.json" in result.stderr
    assert "continuing without coverage" in result.stderr


def test_doctor_and_check_agree_about_a_missing_configured_coverage_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two must not disagree about whether a setup is usable.

    `doctor` FAILed (exit 1) on the very setup `check` passed (exit 0). Both now key
    off the same rule: auto-coverage can substitute, so it is a warning; it cannot, so
    it is fatal.
    """
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    config = tmp_path / "pyproject.toml"
    config.write_text('[tool.riskratchet]\npaths = ["src"]\n', encoding="utf-8")
    _seed_baseline(src)  # else doctor FAILs on the baseline check instead

    # Auto-coverage can substitute: a fresh cache exists, so no test command runs.
    cache = tmp_path / ".riskratchet" / "coverage.json"
    cache.parent.mkdir()
    cache.write_text(
        '{"files": {"src/m.py": {"executed_lines": [1, 2], "missing_lines": []}}}', encoding="utf-8"
    )
    config.write_text(
        '[tool.riskratchet]\npaths = ["src"]\ncoverage = "ci-coverage.json"\n', encoding="utf-8"
    )
    assert runner.invoke(app, ["doctor"]).exit_code == 0
    assert runner.invoke(app, ["scan", str(src), "--no-git"]).exit_code == 0

    config.write_text(
        '[tool.riskratchet]\npaths = ["src"]\ncoverage = "ci-coverage.json"\nauto_coverage = false\n',
        encoding="utf-8",
    )
    assert runner.invoke(app, ["doctor"]).exit_code == 1
    assert runner.invoke(app, ["check", str(src), "--no-git"]).exit_code == 2


@pytest.mark.parametrize(
    "payload", [None, '{"meta": {}, "files": {}, "totals": {}}'], ids=["absent", "wrong-format"]
)
def test_a_typescript_coverage_report_the_user_named_must_be_usable(
    payload: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable `--ts-coverage` used to warn and score on an empty coverage view.

    Under `missing_coverage = skip` that dropped every TypeScript function and still
    exited 0 — the gate switched off by a typo, which is what `0.3.4` closed for the
    Python side.
    """
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    report = tmp_path / "ts-coverage.json"
    if payload is not None:
        report.write_text(payload, encoding="utf-8")

    result = runner.invoke(
        app, ["scan", str(src), "--typescript", "--ts-coverage", str(report), "--no-auto-cov", "--no-git"]
    )

    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize(
    "args",
    [
        ["scan", "--format", "sarif", "--output"],
        ["scan", "--debug-json-file"],
        ["baseline", "--allow-missing-coverage", "--output"],
    ],
    ids=["report", "debug-json", "baseline"],
)
def test_an_unwritable_output_path_is_a_setup_error_not_a_gate_failure(
    args: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 1 means "a gate tripped". An `IsADirectoryError` is not a gate tripping.

    `scan`'s own docstring says it never fails, and it was returning 1 with a raw
    traceback whenever `--output` pointed at a directory or a read-only location.
    """
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    blocked = tmp_path / "blocked"
    blocked.mkdir()

    result = runner.invoke(app, [args[0], str(src), *args[1:], str(blocked), "--no-auto-cov", "--no-git"])

    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.output
    assert "could not write" in result.stderr


def test_allow_missing_coverage_downgrades_the_named_path_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The escape hatch stays an escape hatch: it warns and continues, never silently."""
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[tool.riskratchet]\npaths = ["src"]\n', encoding="utf-8")
    _seed_baseline(src)

    result = runner.invoke(
        app,
        [
            "check",
            str(src),
            "--coverage",
            "absent.json",
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "coverage file not found: absent.json" in result.stderr


# --- 0.3.5: the config you wrote is the config that runs ----------------------------


@pytest.mark.parametrize("command", ["scan", "check", "diff", "baseline"])
def test_include_from_config_narrows_the_scan(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`include` was declared, validated, schema'd, documented — and read by nobody.

    Every command did `include or []` while the adjacent lines correctly did
    `exclude or cfg.get("exclude", [])`. `doctor` *did* read it, so its coverage-overlap
    check evaluated a narrower file set than the run it was diagnosing.
    """
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.riskratchet]\npaths = ["src"]\ninclude = ["src/m.py"]\nfail_new_above = 5\n',
        encoding="utf-8",
    )
    _seed_baseline(src)  # *before* other.py exists, so a leak makes it a NEW function
    # Deliberately risky: uncovered, public, and branchy enough to score above the
    # default `fail_new_above`. If `include` leaks, `check` fails on it — so a clean
    # exit is positive evidence the filter applied, not merely an absent string.
    (src / "other.py").write_text(
        "def other(a, b, c, d, e, f, g, h):\n"
        + "".join(f"    if {v}:\n        return {i}\n" for i, v in enumerate("abcdefgh"))
        + "    return 0\n",
        encoding="utf-8",
    )

    args = [command, "--no-auto-cov", "--no-git"]
    if command != "scan":  # `scan` has no --allow-missing-coverage
        args.append("--allow-missing-coverage")
    if command != "baseline":  # `baseline` writes a file rather than emitting a report
        args.append("--json")
    result = runner.invoke(app, args)

    assert result.exit_code == 0, result.output
    haystack = (
        (tmp_path / ".riskratchet.json").read_text(encoding="utf-8")
        if command == "baseline"
        else result.stdout
    )
    assert "other" not in haystack


def test_an_allow_target_copied_from_a_report_actually_suppresses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`path::qualname` is the form riskratchet prints, and the one that never matched.

    Patterns were matched against the path *or* the qualname, never the target, so
    pasting a target straight out of a report suppressed nothing — silently, while also
    failing to keep it out of the baseline.
    """
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.riskratchet]\npaths = ["src"]\nallow = ["src/m.py::trivial"]\n', encoding="utf-8"
    )

    result = runner.invoke(app, ["scan", "--json", "--no-auto-cov", "--no-git"])

    assert result.exit_code == 0, result.output
    assert '"qualname": "trivial"' not in result.stdout
    assert "nothing was suppressed" not in result.stderr


def test_allow_patterns_that_suppress_nothing_say_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A suppression that suppresses nothing is debt the user believes is parked."""
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.riskratchet]\npaths = ["src"]\nallow = ["src/m.py::misspelled"]\n', encoding="utf-8"
    )

    result = runner.invoke(app, ["scan", "--no-auto-cov", "--no-git"])

    assert result.exit_code == 0, result.output
    assert "nothing was suppressed" in result.stderr


def test_a_baseline_whose_entries_all_fail_to_read_is_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero usable entries is a zero-entry baseline, and those pass every gate.

    `load_baseline`'s own docstring says a file that cannot be trusted as a ratchet must
    fail loudly; it applied that to a missing `entries` array but not to an `entries`
    array none of whose members parsed. It compounds: `check` then feeds `0` into
    `_require_gateable_functions`, so one condition disabled two guards.
    """
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    baseline = tmp_path / "all-broken.json"
    baseline.write_text(
        '{"version": "3", "entries": ['
        '{"path": "src/m.py", "qualname": "a", "score": "nope", "components": {}},'
        '{"path": "src/m.py", "qualname": "b", "score": "nope", "components": {}}'
        "]}",
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "check",
            str(src),
            "--baseline",
            str(baseline),
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )

    assert result.exit_code == 2, result.output
    assert "none could be read" in result.stderr
    assert "No risk regressions detected" not in result.output


def test_doctor_does_not_call_a_zero_entry_baseline_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty baseline passes every gate, so PASS is the wrong word for it."""
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[tool.riskratchet]\npaths = ["src"]\n', encoding="utf-8")
    (tmp_path / ".riskratchet.json").write_text('{"version": "3", "entries": []}', encoding="utf-8")

    result = runner.invoke(app, ["doctor", "--json"])

    assert '"status": "warn"' in result.stdout
    assert "no entries" in result.stdout


def test_explain_resolves_the_same_target_from_a_nested_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`explain` computed identity against the cwd, not the project root.

    So run from a nested package it rejected the very `path::qualname` that `check` and
    `diff` had just printed — breaking `_discover_config`'s promise that a nested run
    matches a root-level one.
    """
    src = _project(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[tool.riskratchet]\npaths = ["src"]\n', encoding="utf-8")
    target = "src/m.py::trivial"

    monkeypatch.chdir(tmp_path)
    from_root = runner.invoke(app, ["explain", target, "--no-auto-cov", "--no-git"])
    monkeypatch.chdir(src)
    from_nested = runner.invoke(
        app, ["explain", target, "--config", str(tmp_path / "pyproject.toml"), "--no-auto-cov", "--no-git"]
    )

    assert from_root.exit_code == 0, from_root.output
    assert from_nested.exit_code == 0, from_nested.output
    assert target in from_nested.stdout


def test_a_target_in_the_wrong_spelling_names_the_right_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Targets are repo-relative; a cwd-relative spelling gets pointed at the real one."""
    src = _project(tmp_path)
    (tmp_path / "pyproject.toml").write_text('[tool.riskratchet]\npaths = ["src"]\n', encoding="utf-8")
    monkeypatch.chdir(src)

    result = runner.invoke(
        app,
        [
            "explain",
            "m.py::trivial",
            "--config",
            str(tmp_path / "pyproject.toml"),
            "--no-auto-cov",
            "--no-git",
        ],
    )

    assert result.exit_code == 2, result.output
    assert "src/m.py::trivial" in result.stderr


@pytest.fixture
def real_test_command_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo conftest's `block_auto_coverage_runner` for the runner's own tests.

    The guard exists so tests never shell out to a real `pytest`. Here
    `_default_runner` *is* the code under test, and both commands it is given
    fail before any process starts — one at `shlex.split`, the other because
    the executable does not exist — so nothing is spawned.
    """
    monkeypatch.setattr(auto_coverage, "_default_runner", _real_default_runner)


@pytest.mark.parametrize("command", ["scan", "baseline"])
def test_an_unrunnable_test_command_exits_two_without_a_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    real_test_command_runner: None,
) -> None:
    """A missing test runner is a setup error, not a gate verdict.

    Auto-coverage shells out to `pytest` by default. On a machine without it —
    a slim CI image, a project using a different runner — `subprocess.run`
    raised an uncaught `FileNotFoundError`, which surfaced as a Rich traceback
    and exit 1. Exit 1 means "risk regressed", so an unrunnable test command
    read as a failing gate.
    """
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.riskratchet]\npaths = ["src"]\ntest_command = "riskratchet-no-such-runner --out {output}"\n',
        encoding="utf-8",
    )

    result = runner.invoke(app, [command, str(src), "--no-git"])

    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.output
    assert "could not be run" in result.output
    # The remediation names all three ways out.
    assert "--coverage" in result.output
    assert "test_command" in result.output
    assert "--no-auto-cov" in result.output


def test_an_unparseable_test_command_exits_two_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_test_command_runner: None
) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.riskratchet]\npaths = ["src"]\ntest_command = "pytest --cov \'unclosed"\n',
        encoding="utf-8",
    )

    result = runner.invoke(app, ["scan", str(src), "--no-git"])

    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.output
    assert "could not be parsed" in result.output


# --- 0.3.6: TypeScript through every door -----------------------------------------

_TS_SRC = (
    "export function handler(value: number): number {\n"
    "  if (value > 0) {\n"
    "    return value;\n"
    "  }\n"
    "  return -value;\n"
    "}\n"
)


def _typescript_only_project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.ts").write_text(_TS_SRC, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.riskratchet]\npaths = ["src"]\ntypescript = true\n', encoding="utf-8"
    )
    return tmp_path / "src"


def test_a_typescript_only_tree_needs_no_python_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With nothing to cover, Python coverage is not a requirement.

    Every command on a TypeScript-only tree used to run auto-coverage: a full
    `pytest --cov` to measure zero functions, or exit 2 where pytest was not installed
    at all — so a TypeScript adopter could not `scan`, `baseline`, or `check`, and
    `--allow-missing-coverage` did not help because the unstartable runner failed
    first. No `--no-auto-cov` here on purpose: the conftest guard turns any attempt to
    run the test command into a failure, so exit 0 proves nothing was spawned.
    """
    pytest.importorskip("tree_sitter_typescript")
    monkeypatch.chdir(tmp_path)
    src = _typescript_only_project(tmp_path)

    scan = runner.invoke(app, ["scan", str(src), "--no-git"])
    assert scan.exit_code == 0, scan.output
    assert "Python coverage not applicable" in scan.stderr

    baseline = runner.invoke(app, ["baseline", str(src), "--no-git"])
    assert baseline.exit_code == 0, baseline.output
    assert "wrote baseline with 1 functions" in baseline.stdout

    check = runner.invoke(app, ["check", str(src), "--no-git"])
    assert check.exit_code == 0, check.output
    # `doctor` and `check` must agree the setup is usable.
    assert runner.invoke(app, ["doctor"]).exit_code == 0


def test_an_unstartable_test_command_warns_under_allow_missing_coverage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, real_test_command_runner: None
) -> None:
    """`allow_missing_coverage` promises "continue when coverage is absent".

    A runner that cannot start yields exactly that absence; before 0.3.6 the flag
    never reached this branch and the run exited 2 regardless.
    """
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.riskratchet]\npaths = ["src"]\nallow_missing_coverage = true\n'
        'test_command = "riskratchet-no-such-runner --out {output}"\n',
        encoding="utf-8",
    )

    result = runner.invoke(app, ["baseline", str(src), "--no-git"])

    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output
    assert "could not be run" in result.stderr
    assert "continuing without coverage" in result.stderr


def test_explain_without_the_extra_exits_two_with_the_install_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`explain` was the one command outside `_build_report_or_exit`.

    Without the `[typescript]` extra it raised a raw `ImportError` — a traceback and
    exit 1, the code that means "risk regressed" — where every other command printed
    the install command and exited 2.
    """
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    monkeypatch.setitem(sys.modules, "tree_sitter_typescript", None)
    monkeypatch.chdir(tmp_path)
    _typescript_only_project(tmp_path)

    result = runner.invoke(app, ["explain", "src/m.ts::handler", "--no-git", "--no-auto-cov"])

    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.output
    assert "riskratchet[typescript]" in result.output


def test_a_configured_typescript_report_that_is_missing_stops_the_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing can substitute for a TypeScript report, so a gate never continues past it.

    The Python `coverage` default warns because auto-coverage may fill it; there is no
    TypeScript auto-coverage, so on `check` a configured `ts_coverage` that does not
    exist is exit 2 — downgraded only by `allow_missing_coverage`.
    """
    monkeypatch.chdir(tmp_path)
    src = _typescript_only_project(tmp_path)
    _seed_baseline(src)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.riskratchet]\npaths = ["src"]\ntypescript = true\nts_coverage = ["coverage/lcov.info"]\n',
        encoding="utf-8",
    )

    result = runner.invoke(app, ["check", str(src), "--no-git", "--no-auto-cov"])

    assert result.exit_code == 2, result.output
    assert "Traceback" not in result.output
    assert "coverage/lcov.info" in result.stderr
    assert "--allow-missing-coverage" in result.stderr


def test_a_configured_typescript_report_that_is_missing_only_warns_on_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`scan` has no `--allow-missing-coverage`; a fresh clone must still be able to look."""
    pytest.importorskip("tree_sitter_typescript")
    monkeypatch.chdir(tmp_path)
    src = _typescript_only_project(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.riskratchet]\npaths = ["src"]\ntypescript = true\nts_coverage = ["coverage/lcov.info"]\n',
        encoding="utf-8",
    )

    result = runner.invoke(app, ["scan", str(src), "--no-git", "--no-auto-cov", "--json"])

    assert result.exit_code == 0, result.output
    assert "Scoring TypeScript without it" in result.stderr
    assert json.loads(result.stdout)["functions"][0]["path"] == "src/m.ts"


def test_a_typescript_report_named_on_the_command_line_stops_check_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    src = _typescript_only_project(tmp_path)
    _seed_baseline(src)

    result = runner.invoke(
        app, ["check", str(src), "--typescript", "--ts-coverage", "nope.info", "--no-git", "--no-auto-cov"]
    )

    assert result.exit_code == 2, result.output
    assert "nope.info" in result.stderr


def test_allow_missing_coverage_tolerates_a_missing_typescript_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The remediation `--allow-missing-coverage` printed did not work before 0.3.6.

    The guard returned early under the flag and the strict loader in
    `typescript_engine` then exited 2 on the same path with a different message.
    """
    pytest.importorskip("tree_sitter_typescript")
    monkeypatch.chdir(tmp_path)
    src = _typescript_only_project(tmp_path)
    _seed_baseline(src)

    result = runner.invoke(
        app,
        [
            "check",
            str(src),
            "--typescript",
            "--ts-coverage",
            "nope.info",
            "--allow-missing-coverage",
            "--no-git",
            "--no-auto-cov",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "nope.info" in result.stderr
