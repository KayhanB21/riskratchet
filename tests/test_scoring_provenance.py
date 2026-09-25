"""What produced this number, and against what contract (0.3.7).

A ratchet compares two scores as if they measured the same thing. They only do when the
same scoring model, resolved weights, churn window, churn availability and coverage
presence produced both — and until 0.3.7 a baseline recorded none of that. A 0.2.x
baseline, written before 0.3.0 redefined `sprawl`, gated a 0.3.x run silently and
reported it clean.

Every disclosure here warns and nothing more, at all three doors. A mismatch means the
*comparison* is untrustworthy; failing on it would break the upgrade it exists to protect.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from textwrap import dedent
from typing import TYPE_CHECKING, Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from typer.testing import CliRunner

from riskratchet.baseline import (
    baseline_from_report,
    baseline_scored_without_churn,
    load_baseline,
    save_baseline,
    scoring_model_stale,
)
from riskratchet.cli import app
from riskratchet.models import (
    Baseline,
    ChurnStats,
    ComplexityStats,
    CoverageStats,
    FileStats,
    FunctionId,
    FunctionRisk,
    FunctionSpan,
    RiskComponents,
    RiskReport,
    ScoringInputs,
)
from riskratchet.schemas import load_schema
from riskratchet.scoring import DEFAULT_WEIGHTS, SCORING_MODEL_VERSION

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


def _inputs(**overrides: Any) -> ScoringInputs:
    base: dict[str, Any] = {
        "model": SCORING_MODEL_VERSION,
        "weights": dict(DEFAULT_WEIGHTS),
        "churn_window_days": 90,
        "churn_available": True,
    }
    base.update(overrides)
    return ScoringInputs(**base)


def _fn(name: str = "f", *, churn: float = 0.0) -> FunctionRisk:
    return FunctionRisk(
        id=FunctionId(path="a.py", qualname=name),
        span=FunctionSpan(start_line=1, end_line=3),
        is_public=True,
        complexity=ComplexityStats(cyclomatic=1),
        coverage=CoverageStats(line_coverage=1.0, branch_coverage=None),
        churn=ChurnStats(commits=0),
        file_stats=FileStats(path="a.py", total_lines=3, function_count=1),
        components=RiskComponents(0.0, 0.0, 0.0, churn, 0.0, 0.0),
        score=10.0,
        crap=1.0,
        language="python",
    )


def _report(*, churn: float = 0.0, coverage: str = "present", **overrides: Any) -> RiskReport:
    return RiskReport(
        functions=(_fn(churn=churn),),
        files=(),
        coverage_status=coverage,
        scoring=_inputs(**overrides),
    )


# --------------------------------------------------------------------------- the block itself


def test_every_baseline_records_what_scored_it_including_python_only() -> None:
    """`identity` is written only for TypeScript; provenance is written for everything. A
    Python-only baseline from a different scoring model is exactly as incomparable."""
    baseline = baseline_from_report(_report())
    assert baseline.identity == {}
    assert baseline.scoring == {
        "model": SCORING_MODEL_VERSION,
        "weights": {name: round(value, 6) for name, value in sorted(DEFAULT_WEIGHTS.items())},
        "churn_window_days": 90,
        "churn_available": True,
        "coverage": "present",
    }


def test_the_block_validates_against_the_published_schema(tmp_path: Path) -> None:
    out = tmp_path / "b.json"
    save_baseline(baseline_from_report(_report()), out)
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert "scoring" in raw
    Draft202012Validator(load_schema("baseline")).validate(raw)


def test_a_load_save_round_trip_cannot_strip_provenance(tmp_path: Path) -> None:
    """`Baseline` and `_dumps` both carry it, so a round-trip through the public API does not
    turn a baseline that knows what scored it into one that does not."""
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    save_baseline(baseline_from_report(_report()), first)
    save_baseline(load_baseline(first), second)
    assert json.loads(second.read_text())["scoring"] == json.loads(first.read_text())["scoring"]


def test_a_report_without_inputs_writes_no_block_rather_than_claiming_defaults(tmp_path: Path) -> None:
    """A hand-built report has no `ScoringInputs`. Writing "default weights" for it would be a
    provenance claim nothing verified — worse than no claim at all."""
    baseline = baseline_from_report(RiskReport(functions=(_fn(),), files=()))
    assert baseline.scoring == {}
    out = tmp_path / "b.json"
    save_baseline(baseline, out)
    assert "scoring" not in json.loads(out.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- what counts as stale


def test_a_v3_baseline_written_the_same_way_is_silent() -> None:
    assert scoring_model_stale(baseline_from_report(_report()), _report()) == []


@pytest.mark.parametrize("version", ["1", "2"])
def test_a_pre_0_3_0_envelope_is_evidence_on_its_own(version: str) -> None:
    """v1/v2 predate the 0.3.0 `sprawl` redefinition, so the envelope alone says the scores
    came from a model this build no longer implements."""
    baseline = Baseline(version=version, entries={}, declared_version=version)
    (reason,) = scoring_model_stale(baseline, _report())
    assert "0.2.x" in reason and f"v{version}" in reason


def test_a_baseline_with_no_version_key_at_all_still_warns() -> None:
    """The oldest files in existence. `_baseline_version` reads a missing key as the current
    version so every reader keeps working; collapsing the two would have made these silent."""
    baseline = Baseline(version="3", entries={}, declared_version=None)
    (reason,) = scoring_model_stale(baseline, _report())
    assert "predates the `version` field" in reason


def test_a_file_with_no_version_key_loads_as_unversioned(tmp_path: Path) -> None:
    """The load path, not just the dataclass: `_baseline_version` must report the absence
    separately from the current version it substitutes, or a file with no `version` key
    arrives indistinguishable from a fresh one and the warning above can never fire."""
    out = tmp_path / "b.json"
    save_baseline(baseline_from_report(_report()), out)
    raw = json.loads(out.read_text(encoding="utf-8"))
    raw.pop("version")
    raw.pop("scoring")
    out.write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_baseline(out)
    assert loaded.declared_version is None
    assert loaded.version == "3"  # still readable: every other caller sees the current version
    (reason,) = scoring_model_stale(loaded, _report())
    assert "predates the `version` field" in reason


def test_a_v3_baseline_from_0_3_0_through_0_3_6_stays_silent() -> None:
    """v3 implies the current scoring model, so there is nothing to report — and nagging every
    existing user on upgrade is precisely what this feature must not do."""
    assert scoring_model_stale(Baseline(version="3", entries={}, declared_version="3"), _report()) == []


def test_changed_weights_name_the_component_that_moved() -> None:
    """ "Re-baseline, that was deliberate" and "fix CI, do not re-baseline" are opposite
    responses, so an opaque digest mismatch would be the wrong thing to report."""
    baseline = baseline_from_report(_report(weights={**DEFAULT_WEIGHTS, "churn": 0.5}))
    (reason,) = scoring_model_stale(baseline, _report())
    assert "weights changed" in reason and "churn" in reason


def test_churn_that_was_available_and_is_not_says_do_not_re_baseline() -> None:
    baseline = baseline_from_report(_report(churn_available=True))
    (reason,) = scoring_model_stale(baseline, _report(churn_available=False))
    assert "every churn component scored 0" in reason
    assert "rather than re-baselining" in reason


def test_a_changed_churn_window_and_coverage_presence_are_both_reported() -> None:
    baseline = baseline_from_report(_report(churn_window_days=30, coverage="present"))
    reasons = scoring_model_stale(baseline, _report(churn_window_days=90, coverage="missing"))
    assert any("churn window 30d -> 90d" in r for r in reasons)
    assert any("coverage present -> missing" in r for r in reasons)


def test_a_changed_scoring_model_is_reported() -> None:
    baseline = baseline_from_report(_report(model=SCORING_MODEL_VERSION + 1))
    (reason,) = scoring_model_stale(baseline, _report())
    assert f"scoring model {SCORING_MODEL_VERSION + 1} -> {SCORING_MODEL_VERSION}" in reason


# --------------------------------------------------------------------------- the all-churn-zero signal


def test_a_baseline_of_all_zero_churn_against_a_run_with_churn_is_flagged() -> None:
    """The one silent zero a pre-0.3.7 baseline still betrays: written where git was
    unreachable, so every churn component is 0 and a real run looks like a mass regression."""
    dead = baseline_from_report(RiskReport(functions=(_fn(churn=0.0),), files=()))
    assert baseline_scored_without_churn(dead, _report(churn=40.0)) is True


def test_a_quiet_repository_is_not_flagged() -> None:
    """Narrow by construction: no measured churn on either side says nothing at all."""
    dead = baseline_from_report(RiskReport(functions=(_fn(churn=0.0),), files=()))
    assert baseline_scored_without_churn(dead, _report(churn=0.0)) is False
    assert baseline_scored_without_churn(Baseline(version="3", entries={}), _report(churn=40.0)) is False


def test_a_baseline_that_already_has_churn_is_not_flagged() -> None:
    live = baseline_from_report(RiskReport(functions=(_fn(churn=20.0),), files=()))
    assert baseline_scored_without_churn(live, _report(churn=40.0)) is False


# --------------------------------------------------------------------------- the three doors

_ARGS = ["--allow-missing-coverage", "--no-auto-cov", "--no-git"]


def _cli_project(tmp_path: Path) -> tuple[Path, Path]:
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text(
        dedent(
            """
            def branchy(x):
                if x > 0:
                    return 1
                return 0
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    baseline = tmp_path / "baseline.json"
    result = runner.invoke(app, ["baseline", str(src), "--output", str(baseline), *_ARGS])
    assert result.exit_code == 0, (result.stdout, result.stderr)
    return src, baseline


def _edit_scoring(baseline: Path, **changes: Any) -> None:
    raw = json.loads(baseline.read_text(encoding="utf-8"))
    raw.setdefault("scoring", {}).update(changes)
    baseline.write_text(json.dumps(raw, indent=2), encoding="utf-8")


def test_check_says_when_the_baseline_was_scored_differently(tmp_path: Path) -> None:
    src, baseline = _cli_project(tmp_path)
    _edit_scoring(baseline, churn_window_days=30)
    result = runner.invoke(app, ["check", str(src), "--baseline", str(baseline), *_ARGS])
    assert result.exit_code == 0, result.stderr  # a warning, never a gate
    assert "was not scored the way this run scores" in result.stderr
    assert "churn window 30d -> 90d" in result.stderr
    assert f"re-baseline: riskratchet baseline {src}" in result.stderr


def test_the_rebaseline_command_does_not_offer_typescript_to_a_python_project(tmp_path: Path) -> None:
    """`--typescript` on a project without the extra is a command that exits 2 when pasted."""
    src, baseline = _cli_project(tmp_path)
    _edit_scoring(baseline, churn_window_days=30)
    result = runner.invoke(app, ["check", str(src), "--baseline", str(baseline), *_ARGS])
    line = next(ln for ln in result.stderr.splitlines() if "re-baseline:" in ln)
    assert "--typescript" not in line
    assert f"--output {baseline}" in line


def test_check_stays_silent_on_a_baseline_it_just_wrote(tmp_path: Path) -> None:
    src, baseline = _cli_project(tmp_path)
    result = runner.invoke(app, ["check", str(src), "--baseline", str(baseline), *_ARGS])
    assert "was not scored the way this run scores" not in result.stderr
    assert "zero churn" not in result.stderr


def test_check_stays_silent_on_a_v3_baseline_that_predates_provenance(tmp_path: Path) -> None:
    """The 0.3.0-0.3.6 shape. Every existing adopter is holding one of these on upgrade."""
    src, baseline = _cli_project(tmp_path)
    raw = json.loads(baseline.read_text(encoding="utf-8"))
    raw.pop("scoring")
    baseline.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    result = runner.invoke(app, ["check", str(src), "--baseline", str(baseline), *_ARGS])
    assert result.exit_code == 0, result.stderr
    assert "was not scored the way this run scores" not in result.stderr


def test_check_warns_on_a_baseline_with_no_version_key(tmp_path: Path) -> None:
    """The oldest files of all, reaching the gate through the real load path."""
    src, baseline = _cli_project(tmp_path)
    raw = json.loads(baseline.read_text(encoding="utf-8"))
    raw.pop("version")
    raw.pop("scoring")
    baseline.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    result = runner.invoke(app, ["check", str(src), "--baseline", str(baseline), *_ARGS])
    assert result.exit_code == 0, result.stderr
    assert "predates the `version` field" in result.stderr


def test_check_warns_on_a_v2_baseline(tmp_path: Path) -> None:
    src, baseline = _cli_project(tmp_path)
    raw = json.loads(baseline.read_text(encoding="utf-8"))
    raw["version"] = "2"
    raw.pop("scoring")
    baseline.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    result = runner.invoke(app, ["check", str(src), "--baseline", str(baseline), *_ARGS])
    assert result.exit_code == 0, result.stderr
    assert "riskratchet 0.2.x" in result.stderr


def test_diff_carries_the_same_disclosure_as_check(tmp_path: Path) -> None:
    """Both commands route through `_apply_baseline_guards`, so neither can grow the
    disclosure without the other."""
    src, baseline = _cli_project(tmp_path)
    _edit_scoring(baseline, churn_window_days=30)
    result = runner.invoke(app, ["diff", str(src), "--baseline", str(baseline), *_ARGS])
    assert "was not scored the way this run scores" in result.stderr


def test_doctor_reports_the_scoring_model_on_every_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cli_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor", "--json"])
    payload = json.loads(result.stdout)
    row = next(check for check in payload["checks"] if check["name"] == "scoring-model")
    assert row["status"] == "pass"
    assert f"model {SCORING_MODEL_VERSION}" in row["summary"]
    Draft202012Validator(load_schema("doctor")).validate(payload)


def test_doctor_and_check_agree_that_a_baseline_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two must not disagree about whether the numbers are comparable. `doctor` resolves
    the inputs from config without building a report; `check` reads them off the report it
    just built. This pins them together."""
    src, baseline = _cli_project(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        dedent(
            f"""
            [tool.riskratchet]
            paths = ["src"]
            baseline = "{baseline.name}"
            churn_window_days = 45
            allow_missing_coverage = true
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    doctor = runner.invoke(app, ["doctor", "--json"])
    row = next(c for c in json.loads(doctor.stdout)["checks"] if c["name"] == "scoring-model")
    assert row["status"] == "warn"
    assert "churn window 90d -> 45d" in row["summary"]

    check = runner.invoke(app, ["check", str(src), "--baseline", str(baseline), *_ARGS[:1], "--no-auto-cov"])
    assert "churn window 90d -> 45d" in check.stderr


def test_doctor_warns_but_never_fails_on_a_stale_scoring_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _cli_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    _edit_scoring(tmp_path / "baseline.json", model=SCORING_MODEL_VERSION + 99)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.riskratchet]\npaths = ["src"]\nbaseline = "baseline.json"\n', encoding="utf-8"
    )
    result = runner.invoke(app, ["doctor", "--json"])
    row = next(c for c in json.loads(result.stdout)["checks"] if c["name"] == "scoring-model")
    assert row["status"] == "warn"
    assert row["remediation"] is not None and "riskratchet baseline" in row["remediation"]
    assert result.exit_code != 2


def test_the_plugin_says_the_same_thing_the_cli_says(tmp_path: Path) -> None:
    """The third door. The plugin has no exit 2 available — a session failure is
    `session.exitstatus = 1`, which means "a gate tripped" — so this is a message and nothing
    more. It must still be the same message, or two doors disagree about one baseline.

    Run out-of-process because the plugin hooks a whole pytest session; `.riskratchet.json` is
    the default name because the plugin resolves its baseline from `--riskratchet-baseline`
    only and never consults `[tool.riskratchet] baseline` (a separate door divergence, not
    this change's to make).
    """
    src, _ = _cli_project(tmp_path)
    baseline = tmp_path / ".riskratchet.json"
    result = runner.invoke(app, ["baseline", str(src), "--output", str(baseline), *_ARGS])
    assert result.exit_code == 0, (result.stdout, result.stderr)
    _edit_scoring(baseline, churn_window_days=30)
    (tmp_path / "pyproject.toml").write_text(
        dedent(
            """
            [tool.riskratchet]
            paths = ["src"]
            allow_missing_coverage = true
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "test_smoke.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "riskratchet",
            "--riskratchet",
            "-q",
            "--cov=src",
            "--cov-report=json:coverage.json",
            "test_smoke.py",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "was not scored the way this run scores" in completed.stdout, completed.stdout
    assert "churn window 30d -> 90d" in completed.stdout
    assert "re-baseline: riskratchet baseline" in completed.stdout


def _git_env(root: Path) -> dict[str, str]:
    """A git environment with the caller's own stripped out.

    Every `GIT_*` variable is dropped rather than inherited. Run under a `git commit` — the
    repo's own pre-commit hook does exactly this — the ambient `GIT_INDEX_FILE`, `GIT_DIR`
    and friends point at the *outer* repository, so a fixture repo built with them inherited
    fails with "Error building trees". The user identity is set per-repo and both config
    layers are neutralised so a developer's global git config cannot change the outcome.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = str(root / ".gitconfig")
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    return env


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, env=_git_env(root), capture_output=True)


def _init_repo(root: Path) -> None:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "Tester")
    _git(root, "config", "commit.gpgsign", "false")


def _commit_all(root: Path, message: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)


def test_check_flags_a_baseline_whose_churn_is_dead_against_a_run_that_has_some(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The end-to-end all-churn-zero disclosure, through a real repository.

    This is the only silent zero a 0.3.0-0.3.6 baseline still betrays: written where git was
    unreachable, every churn component is 0, and comparing a real run against it pushes churn
    up everywhere at once. Re-baselining is the *wrong* remedy, so it gets its own wording.
    """
    src = tmp_path / "src"
    src.mkdir()
    target = src / "m.py"
    target.write_text("def branchy(x):\n    if x > 0:\n        return 1\n    return 0\n", encoding="utf-8")
    # Drop any inherited GIT_* pointing at an outer repository. The repo's own pre-commit hook
    # runs this suite from inside a `git commit`, where `GIT_DIR` and `GIT_INDEX_FILE` are set;
    # riskratchet's churn collection shells out to git and would read that repo, not this one.
    for key in [name for name in os.environ if name.startswith("GIT_")]:
        monkeypatch.delenv(key, raising=False)
    _init_repo(tmp_path)
    _commit_all(tmp_path, "first")
    target.write_text(
        "def branchy(x):\n    if x > 0:\n        return 1\n    if x < 0:\n        return -1\n    return 0\n",
        encoding="utf-8",
    )
    _commit_all(tmp_path, "second")
    # Anchored inside the fixture so the config dir, the scan root and the git root are all
    # this repository — churn is collected relative to the config directory.
    monkeypatch.chdir(tmp_path)

    # A baseline scored with churn switched off: every entry carries churn 0.
    baseline = tmp_path / "dead.json"
    written = runner.invoke(
        app,
        [
            "baseline",
            "src",
            "--output",
            str(baseline),
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert written.exit_code == 0, (written.stdout, written.stderr)
    raw = json.loads(baseline.read_text(encoding="utf-8"))
    assert all(entry["components"]["churn"] == 0 for entry in raw["entries"])
    raw.pop("scoring")  # the 0.3.0-0.3.6 shape, so only the zeroes are left as evidence
    baseline.write_text(json.dumps(raw, indent=2), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "check",
            "src",
            "--baseline",
            str(baseline),
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--fail-component-regression-above",
            "100",
        ],
    )
    assert "zero churn but this run measured some" in result.stderr, result.stderr
    assert "git history was unavailable" in result.stderr
    assert "re-baseline: riskratchet baseline" in result.stderr


def test_doctor_reports_unusable_weights_instead_of_crashing_on_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unknown weight key raises out of `resolve_weights`. Letting it escape would exit 1
    on a *setup* problem — the failure mode this release exists to remove — and `doctor`'s job
    is to report bad config, not to die on it."""
    _cli_project(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        dedent(
            """
            [tool.riskratchet]
            paths = ["src"]

            [tool.riskratchet.weights]
            bogus = 1.0
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor", "--json"])
    # `doctor` still exits 1 because the *config* check fails — that is a verdict, not a crash.
    assert isinstance(result.exception, SystemExit), result.exception
    row = next(c for c in json.loads(result.stdout)["checks"] if c["name"] == "scoring-model")
    assert row["status"] == "warn"
    assert "cannot resolve the scoring weights" in row["summary"]


def test_doctor_does_not_judge_coverage_presence_it_cannot_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`doctor` sees whether a coverage file exists now; a run may be about to create one via
    auto-coverage. Comparing it would warn about a difference that will not exist by the time
    anything is scored — and `doctor`'s own coverage row already reports that state."""
    _cli_project(tmp_path)
    baseline = tmp_path / ".riskratchet.json"
    written = runner.invoke(app, ["baseline", "src", "--output", str(baseline), *_ARGS])
    monkeypatch.chdir(tmp_path)
    assert written.exit_code == 0, (written.stdout, written.stderr)
    _edit_scoring(baseline, coverage="present")  # the baseline claims coverage; none is on disk
    (tmp_path / "pyproject.toml").write_text('[tool.riskratchet]\npaths = ["src"]\n', encoding="utf-8")
    result = runner.invoke(app, ["doctor", "--json"])
    row = next(c for c in json.loads(result.stdout)["checks"] if c["name"] == "scoring-model")
    assert row["status"] == "pass", row
    assert "coverage" not in row["summary"]
