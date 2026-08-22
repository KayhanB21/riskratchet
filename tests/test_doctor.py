"""Tests for `riskratchet doctor` (P13).

The JSON envelope is validated against `schemas/doctor.schema.json` in
test_schemas.py; here we drive the diagnose() function and the CLI
command end-to-end to verify per-check outcomes and remediation text.

Some checks are conditional — the coverage-derived ones need a loadable
coverage file and the TypeScript one needs a baseline that records
TypeScript entries — so assertions here pin the *set* of names against
the schema enum rather than a frozen count.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest
from syrupy.assertion import SnapshotAssertion
from typer.testing import CliRunner

from riskratchet.cli import app
from riskratchet.doctor import CheckStatus, DoctorCheck, _check_baseline, diagnose, summarize

runner = CliRunner()

# Emitted on every run, in this order, regardless of project shape.
ALWAYS_PRESENT_CHECKS = ("paths", "baseline", "coverage", "git", "shallow-clone", "config", "suppressions")

# Read from the schema so the code and the published contract cannot drift.
SCHEMA_CHECK_NAMES: tuple[str, ...] = tuple(
    json.loads((Path(__file__).resolve().parents[1] / "schemas" / "doctor.schema.json").read_text())[
        "properties"
    ]["checks"]["items"]["properties"]["name"]["enum"]
)


def _project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("def f(): return 1\n", encoding="utf-8")
    return tmp_path / "src"


def test_diagnose_pass_pass_path() -> None:
    """Smoke: all-pass when everything's set up correctly."""
    # Build a temp-dir via pytest fixture happens at CLI test layer; here
    # we sanity-check the helper independently of the CLI.
    checks = diagnose(
        config_dir=Path("."),
        cfg={"paths": ["src"]},
        paths=[Path("src")] if Path("src").exists() else [Path(".")],
        baseline_file=Path(".riskratchet.json"),
        coverage_path=Path("coverage.json"),
    )
    # We just check the shape — values depend on the cwd state. Coverage-derived
    # and TypeScript checks are conditional, so assert the unconditional core in
    # declaration order rather than freezing a count.
    names = [c.name for c in checks]
    assert set(ALWAYS_PRESENT_CHECKS).issubset(names)
    assert [n for n in names if n in ALWAYS_PRESENT_CHECKS] == list(ALWAYS_PRESENT_CHECKS)
    assert set(names) <= set(SCHEMA_CHECK_NAMES)


def test_doctor_cli_fails_when_baseline_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1, (result.stdout, result.stderr)
    # Status table stays on stdout; remediation routes to stderr.
    assert "baseline" in result.stdout
    assert "FAIL" in result.stdout
    assert "riskratchet baseline" in result.stderr
    assert "riskratchet baseline" not in result.stdout


def test_doctor_cli_passes_when_everything_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    # Create a real baseline so the baseline check passes.
    create = runner.invoke(
        app,
        [
            "baseline",
            str(src),
            "--allow-missing-coverage",
            "--no-auto-cov",
            "--no-git",
        ],
    )
    assert create.exit_code == 0, create.output
    # Initialize a git repo so the git check passes.
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    # Write minimal pyproject so config check passes.
    (tmp_path / "pyproject.toml").write_text(
        dedent(
            """
            [tool.riskratchet]
            paths = ["src"]
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["doctor"])
    # coverage is still warn (no coverage configured); doctor should exit 0
    # because warn is not fail.
    assert result.exit_code == 0, result.output
    assert "FAIL" not in result.stdout


def test_doctor_json_envelope_has_expected_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 1, result.output  # baseline missing
    payload = json.loads(result.stdout)
    assert payload["$schema"].endswith("doctor.schema.json")
    assert isinstance(payload["version"], str)
    names = {c["name"] for c in payload["checks"]}
    assert set(ALWAYS_PRESENT_CHECKS).issubset(names)
    # Every emitted name must be declared in the schema enum, or `--json`
    # validation breaks for consumers.
    assert names <= set(SCHEMA_CHECK_NAMES)
    total = len(payload["checks"])
    summary = payload["summary"]
    assert summary["total"] == total
    assert summary["passed"] + summary["warned"] + summary["failed"] == total
    # baseline missing should be among the failures
    failed = [c for c in payload["checks"] if c["status"] == "fail"]
    assert any(c["name"] == "baseline" for c in failed)
    for check in failed:
        assert check["remediation"], "every failing check must carry a remediation"


def test_doctor_warns_when_coverage_is_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _project(tmp_path)
    # Write a coverage.json that pre-dates the source file.
    old = tmp_path / "coverage.json"
    old.write_text('{"files": {}}', encoding="utf-8")
    import os
    import time

    # Force coverage mtime older than src.
    old_time = time.time() - 600
    os.utime(old, (old_time, old_time))
    (tmp_path / "pyproject.toml").write_text(
        dedent(
            """
            [tool.riskratchet]
            paths = ["src"]
            coverage = "coverage.json"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    # Force a source-file write so it's clearly newer.
    (src / "m.py").write_text("def f(): return 2\n", encoding="utf-8")
    result = runner.invoke(app, ["doctor", "--json"])
    payload = json.loads(result.stdout)
    cov = next(c for c in payload["checks"] if c["name"] == "coverage")
    assert cov["status"] == "warn"
    assert "stale" in cov["summary"]
    assert "pytest --cov" in (cov["remediation"] or "")


def test_find_newer_py_covers_all_branches(tmp_path: Path) -> None:
    # `_find_newer_py` compares `st_mtime`, so its branch coverage is otherwise
    # environment-dependent (which source files happen to be newer than coverage.json
    # during a full test run differs Mac↔Linux) — that made the risk baseline
    # non-reproducible across machines. Exercise every branch with explicitly set mtimes
    # so its coverage is identical everywhere.
    import os

    from riskratchet.doctor import _find_newer_py

    cov_mtime = 1_000_000.0

    # non-existent path is skipped → None
    assert _find_newer_py([tmp_path / "nope.py"], cov_mtime) is None

    # a file that is not .py is skipped
    other = tmp_path / "data.txt"
    other.write_text("x\n", encoding="utf-8")
    os.utime(other, (cov_mtime + 100, cov_mtime + 100))
    assert _find_newer_py([other], cov_mtime) is None

    # a .py file newer than cov_mtime is returned
    newer = tmp_path / "newer.py"
    newer.write_text("a = 1\n", encoding="utf-8")
    os.utime(newer, (cov_mtime + 100, cov_mtime + 100))
    assert _find_newer_py([newer], cov_mtime) == str(newer)

    # a .py file older than cov_mtime is not returned
    older = tmp_path / "older.py"
    older.write_text("b = 1\n", encoding="utf-8")
    os.utime(older, (cov_mtime - 100, cov_mtime - 100))
    assert _find_newer_py([older], cov_mtime) is None

    # a directory containing a newer .py returns that file
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    nested = pkg / "mod.py"
    nested.write_text("c = 1\n", encoding="utf-8")
    os.utime(nested, (cov_mtime + 100, cov_mtime + 100))
    assert _find_newer_py([pkg], cov_mtime) == str(nested)

    # a directory whose .py files are all older → None
    olddir = tmp_path / "olddir"
    olddir.mkdir()
    oldnested = olddir / "old.py"
    oldnested.write_text("d = 1\n", encoding="utf-8")
    os.utime(oldnested, (cov_mtime - 100, cov_mtime - 100))
    assert _find_newer_py([olddir], cov_mtime) is None


def test_check_coverage_covers_all_branches(tmp_path: Path) -> None:
    # `_check_coverage` compares coverage.json's mtime to source mtimes (via `_find_newer_py`),
    # so its fresh-vs-stale branch is otherwise environment-dependent: whether the coverage file
    # happens to be newer than the sources during a full test run differs run to run, so the
    # fresh→PASS branch was only covered *incidentally*. That left `doctor.py::_check_coverage`'s
    # branch coverage flaky (~1 run in 3 dropped it), which moved its risk score and could
    # intermittently red the dogfood/publish gate. Drive every branch with explicit mtimes so its
    # coverage is identical everywhere (mirrors `test_find_newer_py_covers_all_branches`).
    import os

    from riskratchet.doctor import _check_coverage

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    src_file = src_dir / "m.py"
    src_file.write_text("def f():\n    return 1\n", encoding="utf-8")
    cov = tmp_path / "coverage.json"
    cov.write_text('{"files": {}}', encoding="utf-8")
    base = 1_000_000.0

    # 1. no coverage configured → WARN
    assert _check_coverage(None, source_paths=[src_dir])[0].status is CheckStatus.WARN
    # 2. configured but the file doesn't exist → FAIL
    assert _check_coverage(tmp_path / "absent.json", source_paths=[src_dir])[0].status is CheckStatus.FAIL
    # 3. coverage older than a source file → WARN (stale)
    os.utime(cov, (base, base))
    os.utime(src_file, (base + 100, base + 100))
    assert _check_coverage(cov, source_paths=[src_dir])[0].status is CheckStatus.WARN
    # 4. coverage newer than every source file → PASS (fresh) — the previously-flaky branch
    os.utime(src_file, (base - 100, base - 100))
    os.utime(cov, (base, base))
    fresh, _ = _check_coverage(cov, source_paths=[src_dir])
    assert fresh.status is CheckStatus.PASS
    assert "fresh" in fresh.summary


def test_doctor_warns_when_config_unknown_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        dedent(
            """
            [tool.riskratchet]
            paths = ["src"]
            fail_new_abvoe = 40  # typo
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["doctor", "--json"])
    payload = json.loads(result.stdout)
    cfg = next(c for c in payload["checks"] if c["name"] == "config")
    assert cfg["status"] == "warn"
    assert "fail_new_abvoe" in cfg["summary"]


def test_doctor_fail_on_invalid_suppression_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        dedent(
            """
            [tool.riskratchet]
            paths = ["src"]
            allow = ["", "src/legacy/**"]
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["doctor", "--json"])
    payload = json.loads(result.stdout)
    supp = next(c for c in payload["checks"] if c["name"] == "suppressions")
    assert supp["status"] == "fail"


def test_summarize_counts_match_status_distribution() -> None:
    from riskratchet.doctor import DoctorCheck

    checks = [
        DoctorCheck("paths", CheckStatus.PASS, "ok"),
        DoctorCheck("baseline", CheckStatus.FAIL, "missing", remediation="riskratchet baseline"),
        DoctorCheck("coverage", CheckStatus.WARN, "stale"),
        DoctorCheck("git", CheckStatus.PASS, "ok"),
        DoctorCheck("config", CheckStatus.WARN, "unknown key"),
        DoctorCheck("suppressions", CheckStatus.PASS, "0"),
    ]
    s = summarize(checks)
    assert s == {"passed": 3, "warned": 2, "failed": 1, "total": 6}


def _normalise(text: str, tmp_path: Path) -> str:
    """Drop tmp_path leakage so the snapshot is portable across machines."""
    return text.replace(str(tmp_path), "<tmp>")


def test_doctor_with_fail_stdout_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, snapshot: SnapshotAssertion
) -> None:
    """Pins the status-table content (stdout) when one check fails. Together
    with the stderr snapshot below, locks the P13 stdout-vs-stderr routing
    that the contrarian critique flagged as silently regressable."""
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1, (result.stdout, result.stderr)
    assert _normalise(result.stdout, tmp_path) == snapshot


def test_doctor_with_fail_stderr_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, snapshot: SnapshotAssertion
) -> None:
    """Pins the `→ fix:` remediation block (stderr) when one check fails."""
    monkeypatch.chdir(tmp_path)
    _project(tmp_path)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1, (result.stdout, result.stderr)
    assert _normalise(result.stderr, tmp_path) == snapshot


def _checks_by_name(checks: Sequence[DoctorCheck]) -> dict[str, DoctorCheck]:
    return {c.name: c for c in checks}


def _diagnose(
    tmp_path: Path,
    *,
    cfg: dict[str, Any] | None = None,
    coverage: Path | None = None,
) -> dict[str, DoctorCheck]:
    src = tmp_path / "src"
    if not src.exists():
        src.mkdir()
        (src / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    return _checks_by_name(
        diagnose(
            config_dir=tmp_path,
            cfg=cfg if cfg is not None else {"paths": ["src"]},
            paths=[src],
            baseline_file=tmp_path / ".riskratchet.json",
            coverage_path=coverage,
        )
    )


def test_malformed_coverage_fails_instead_of_passing(tmp_path: Path) -> None:
    """The one new FAIL: doctor used to report PASS on a file that crashed `check`.

    It cannot regress a working setup — anything that fails to parse here was
    already breaking `check`.
    """
    cov = tmp_path / "coverage.json"
    cov.write_text("not json", encoding="utf-8")

    check = _diagnose(tmp_path, coverage=cov)["coverage"]

    assert check.status is CheckStatus.FAIL
    assert "malformed" in check.summary
    assert "pytest --cov" in (check.remediation or "")


def test_coverage_without_files_section_fails(tmp_path: Path) -> None:
    cov = tmp_path / "coverage.json"
    cov.write_text('{"meta": {}}', encoding="utf-8")

    assert _diagnose(tmp_path, coverage=cov)["coverage"].status is CheckStatus.FAIL


def test_coverage_overlap_warns_when_measuring_a_different_tree(tmp_path: Path) -> None:
    """0% for everything with no error is the most common real misconfiguration."""
    cov = tmp_path / "coverage.json"
    cov.write_text(
        json.dumps({"files": {"somewhere/else/z.py": {"executed_lines": [1], "missing_lines": []}}}),
        encoding="utf-8",
    )

    check = _diagnose(tmp_path, coverage=cov)["coverage-overlap"]

    assert check.status is CheckStatus.WARN
    assert "0 of 1" in check.summary


def test_coverage_overlap_passes_when_files_match(tmp_path: Path) -> None:
    cov = tmp_path / "coverage.json"
    cov.write_text(
        json.dumps({"files": {"src/m.py": {"executed_lines": [1], "missing_lines": []}}}),
        encoding="utf-8",
    )

    assert _diagnose(tmp_path, coverage=cov)["coverage-overlap"].status is CheckStatus.PASS


def test_branch_data_warns_without_cov_branch(tmp_path: Path) -> None:
    """Missing branch data zeroes branch_gap, so scores come out too low."""
    cov = tmp_path / "coverage.json"
    cov.write_text(
        json.dumps({"files": {"src/m.py": {"executed_lines": [1], "missing_lines": []}}}),
        encoding="utf-8",
    )

    check = _diagnose(tmp_path, coverage=cov)["branch-data"]

    assert check.status is CheckStatus.WARN
    assert "--cov-branch" in (check.remediation or "")


def test_branch_data_passes_with_cov_branch(tmp_path: Path) -> None:
    cov = tmp_path / "coverage.json"
    cov.write_text(
        json.dumps(
            {
                "files": {
                    "src/m.py": {
                        "executed_lines": [1],
                        "missing_lines": [],
                        "executed_branches": [[1, 2]],
                        "missing_branches": [],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    assert _diagnose(tmp_path, coverage=cov)["branch-data"].status is CheckStatus.PASS


def test_shallow_clone_warns(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "shallow").write_text("deadbeef\n", encoding="utf-8")

    check = _diagnose(tmp_path)["shallow-clone"]

    assert check.status is CheckStatus.WARN
    assert "fetch-depth: 0" in (check.remediation or "")


def test_shallow_clone_passes_on_full_history(tmp_path: Path) -> None:
    assert _diagnose(tmp_path)["shallow-clone"].status is CheckStatus.PASS


@pytest.mark.parametrize(
    "cfg",
    [
        {"paths": "src"},
        {"paths": ["src"], "fail_new_above": "50"},
        {"paths": ["src"], "fail_above": 150},
    ],
)
def test_config_check_warns_on_invalid_values(tmp_path: Path, cfg: dict[str, Any]) -> None:
    """`_check_config` only looked for unknown keys, so wrong *types* passed."""
    check = _diagnose(tmp_path, cfg=cfg)["config"]

    assert check.status is CheckStatus.WARN
    assert "invalid config" in check.summary
    assert "config validate" in (check.remediation or "")


def test_config_check_passes_on_valid_config(tmp_path: Path) -> None:
    assert _diagnose(tmp_path, cfg={"paths": ["src"]})["config"].status is CheckStatus.PASS


def test_typescript_check_absent_for_python_only_baseline(tmp_path: Path) -> None:
    """A Python-only baseline records no identity, so no new row appears."""
    (tmp_path / ".riskratchet.json").write_text(json.dumps({"version": 3, "entries": []}), encoding="utf-8")

    assert "typescript" not in _diagnose(tmp_path)


def test_typescript_check_warns_on_grammar_mismatch(tmp_path: Path) -> None:
    (tmp_path / ".riskratchet.json").write_text(
        json.dumps(
            {
                "version": 3,
                "entries": [],
                "identity": {"typescript": {"scheme": 1, "grammar": "0.0.1"}},
            }
        ),
        encoding="utf-8",
    )

    check = _diagnose(tmp_path).get("typescript")

    assert check is not None
    assert check.status is CheckStatus.WARN
    assert "0.0.1" in check.summary
    assert "--typescript" in (check.remediation or "")


def test_future_baseline_version_tells_doctor_to_upgrade_not_regenerate(tmp_path: Path) -> None:
    """Regenerating would overwrite a newer, still-valid baseline with an older format."""
    baseline = tmp_path / "future.json"
    baseline.write_text('{"version": "99", "entries": []}', encoding="utf-8")

    check = _check_baseline(baseline)

    assert check.status is CheckStatus.FAIL
    assert "newer riskratchet" in check.summary
    assert "upgrade" in (check.remediation or "").lower()
    assert "riskratchet baseline" not in (check.remediation or "")


def test_config_check_reports_unknown_keys_and_bad_values_together(tmp_path: Path) -> None:
    """Returning early on an unknown key used to hide every type error behind it.

    A config with a typo *and* a wrong-typed value showed only the typo, so
    fixing it revealed the next problem one run at a time.
    """
    check = _diagnose(tmp_path, cfg={"paths": ["src"], "fail_new_abvoe": 1, "auto_coverage": "yes"})["config"]

    assert check.status is CheckStatus.WARN
    assert "fail_new_abvoe" in check.summary
    assert "auto_coverage must be a boolean." in check.summary


def test_doctor_never_exits_on_a_config_it_exists_to_diagnose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The analysis commands exit 2 on a bad value; `doctor` must still report.

    Bailing out before the table rendered would make the one command whose job
    is explaining a broken setup the one command that refuses to look at it.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.riskratchet]\npaths = ["src"]\nfail_new_above = "1"\n', encoding="utf-8"
    )

    result = runner.invoke(app, ["doctor"])

    # 2 is the "refused to run" code the analysis commands now use for this
    # config; doctor reports instead. (1 here is its normal "a check FAILed"
    # exit — this bare project has no baseline yet.)
    assert result.exit_code != 2, result.output
    assert "fail_new_above must be a number." in result.output
