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

from pathlib import Path
from textwrap import dedent

import pytest
from click.testing import Result
from typer.testing import CliRunner

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
    # The surviving entry is scored generously so nothing regresses and the exit
    # code isolates the one thing under test: a dropped entry is not fatal.
    baseline.write_text(
        '{"version": "3", "entries": ['
        '{"path": "src/m.py", "qualname": "trivial", "score": 100.0, "components": {}},'
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
