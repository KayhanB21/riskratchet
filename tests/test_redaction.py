"""Tests for privacy-aware output redaction (P12).

The headline guarantee is **invariance**: redaction is an output transform
applied after matching, so the ratchet decision (exit code, regression count)
must be identical with and without redaction. We also check that redaction
never leaks an original identifier through a free-text `reason`, and that the
persisted baseline file is never redacted.
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest
from typer.testing import CliRunner

from riskratchet.cli import app
from riskratchet.models import FunctionId
from riskratchet.redaction import (
    RedactionConfig,
    SaltResolution,
    _scrub,
    _target_map,
    redact_function_id,
    resolve_salt,
)

runner = CliRunner()

SIMPLE = "def alpha(x):\n    return x + 1\n"
COMPLEX = dedent(
    """
    def alpha(x):
        total = 0
        for i in range(x):
            if i % 2 == 0:
                total += i
            elif i % 3 == 0:
                total -= i
            else:
                total += 1
        return total
    """
).strip()
RENAMED_SIMPLE = "def beta(x):\n    return x + 1\n"


def _write_project(tmp_path: Path, body: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "redact-fixture"\nversion = "0.0.0"\n', encoding="utf-8"
    )
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / "m.py").write_text(body + "\n", encoding="utf-8")
    return src


# --- unit: hashing -------------------------------------------------------


def test_hash_is_deterministic_and_salt_sensitive() -> None:
    fid = FunctionId(path="src/m.py", qualname="Foo.bar")
    unsalted = RedactionConfig(redact_paths=True, redact_qualnames=True)
    salted = RedactionConfig(redact_paths=True, redact_qualnames=True, salt="s3cret")

    a = redact_function_id(fid, unsalted)
    b = redact_function_id(fid, unsalted)
    c = redact_function_id(fid, salted)

    assert a == b  # deterministic for a fixed (value, salt)
    assert a.path != fid.path and a.qualname != fid.qualname
    assert c.path != a.path  # salt changes the hash


def test_resolve_salt_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RISKRATCHET_REDACT_SALT", "env-salt")
    assert resolve_salt("cli-salt", "cfg-salt") == SaltResolution("cli-salt", "explicit")
    assert resolve_salt(None, "cfg-salt") == SaltResolution("env-salt", "env")
    monkeypatch.delenv("RISKRATCHET_REDACT_SALT")
    assert resolve_salt(None, "cfg-salt") == SaltResolution("cfg-salt", "config")
    # Falls through to the injected auto-deriver, then to "none".
    assert resolve_salt(None, None, auto=lambda: "git-sha") == SaltResolution("git-sha", "auto")
    assert resolve_salt(None, None, auto=lambda: None) == SaltResolution(None, "none")
    assert resolve_salt(None, None) == SaltResolution(None, "none")


def test_redact_function_id_partial() -> None:
    fid = FunctionId(path="src/m.py", qualname="Foo.bar")
    paths_only = redact_function_id(fid, RedactionConfig(redact_paths=True))
    assert paths_only.path != "src/m.py"
    assert paths_only.qualname == "Foo.bar"


def test_scrub_handles_substring_targets() -> None:
    """A target that is a substring of a longer one must not shadow it."""
    cfg = RedactionConfig(redact_qualnames=True, salt="s")
    short = FunctionId(path="m.py", qualname="foo")
    long = FunctionId(path="m.py", qualname="foobar")
    mapping = _target_map([short, long], cfg)
    text = f"could match: {long.as_target()}, {short.as_target()}"
    scrubbed = _scrub(text, mapping)
    # Both originals gone; the longer one wasn't partially mangled by the shorter.
    assert "foo" not in scrubbed
    assert "foobar" not in scrubbed
    assert mapping[long.as_target()] in scrubbed
    assert mapping[short.as_target()] in scrubbed


# --- CLI: invariance of the ratchet decision ----------------------------


def test_check_decision_invariant_with_regression(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _write_project(tmp_path, SIMPLE)
    common = ["--allow-missing-coverage", "--no-auto-cov", "--no-git"]
    runner.invoke(app, ["baseline", str(src), *common])

    # Regress the function: same id, more complexity. The delta is small, so
    # gate at a low regression tolerance to make the regression deterministic.
    (src / "m.py").write_text(COMPLEX + "\n", encoding="utf-8")
    gate = ["--fail-regression-above", "1"]

    plain = runner.invoke(app, ["check", str(src), *common, *gate, "--json"])
    redacted = runner.invoke(
        app, ["check", str(src), *common, *gate, "--json", "--redact-paths", "--redact-qualnames"]
    )

    assert plain.exit_code == redacted.exit_code == 1  # the regression gates both ways
    plain_regs = json.loads(plain.stdout)["regressions"]
    redacted_regs = json.loads(redacted.stdout)["regressions"]
    assert len(plain_regs) == len(redacted_regs) >= 1
    # The kinds/scores match; only the identifier strings differ.
    assert [r["kind"] for r in plain_regs] == [r["kind"] for r in redacted_regs]
    assert [r["current_score"] for r in plain_regs] == [r["current_score"] for r in redacted_regs]
    assert redacted_regs[0]["qualname"] != plain_regs[0]["qualname"]


def test_check_decision_invariant_when_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _write_project(tmp_path, SIMPLE)
    common = ["--allow-missing-coverage", "--no-auto-cov", "--no-git"]
    runner.invoke(app, ["baseline", str(src), *common])

    plain = runner.invoke(app, ["check", str(src), *common, "--json"])
    redacted = runner.invoke(app, ["check", str(src), *common, "--json", "--private-comment"])
    assert plain.exit_code == redacted.exit_code == 0


def test_moved_reason_does_not_leak_original_qualname(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    src = _write_project(tmp_path, SIMPLE)
    common = ["--allow-missing-coverage", "--no-auto-cov", "--no-git"]
    runner.invoke(app, ["baseline", str(src), *common])

    # Rename alpha -> beta, identical body: matched as MOVED.
    (src / "m.py").write_text(RENAMED_SIMPLE, encoding="utf-8")

    redacted = runner.invoke(app, ["diff", str(src), *common, "--json", "--redact-qualnames"])
    assert redacted.exit_code == 0, redacted.output
    payload = redacted.stdout
    # The reason for a MOVED entry embeds "moved from <path>::alpha"; redaction
    # must scrub the original qualname everywhere it appears, including reasons.
    assert "alpha" not in payload
    entries = json.loads(payload)["entries"]
    moved = [e for e in entries if e["status"] == "moved"]
    assert moved, entries
    assert "alpha" not in (moved[0]["reason"] or "")
    assert moved[0]["previous_qualname"] != "alpha"


def test_private_comment_suppresses_source_links(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _write_project(tmp_path, COMPLEX)
    # `scan` never requires coverage, so it does not accept --allow-missing-coverage.
    common = ["--no-auto-cov", "--no-git"]

    with_links = runner.invoke(
        app,
        ["scan", str(src), *common, "--json", "--repo-url", "https://x/r", "--commit-ref", "abc"],
    )
    assert "source_url" in with_links.stdout

    private = runner.invoke(
        app,
        [
            "scan",
            str(src),
            *common,
            "--json",
            "--repo-url",
            "https://x/r",
            "--commit-ref",
            "abc",
            "--private-comment",
        ],
    )
    assert "source_url" not in private.stdout


# --- baseline integrity --------------------------------------------------


def test_baseline_file_is_never_redacted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _write_project(tmp_path, COMPLEX)
    common = ["--allow-missing-coverage", "--no-auto-cov", "--no-git"]

    out_plain = tmp_path / "plain.json"
    runner.invoke(app, ["baseline", str(src), *common, "--output", str(out_plain)])

    # A salt in the environment must not alter the persisted baseline: the
    # baseline command does not accept redaction flags, and writes raw ids.
    monkeypatch.setenv("RISKRATCHET_REDACT_SALT", "should-not-matter")
    out_salted = tmp_path / "salted.json"
    runner.invoke(app, ["baseline", str(src), *common, "--output", str(out_salted)])

    assert out_plain.read_text(encoding="utf-8") == out_salted.read_text(encoding="utf-8")
    # And the raw qualname is present (not hashed) in the baseline.
    assert "alpha" in out_plain.read_text(encoding="utf-8")


# --- ambiguous-rename reason scrub (the riskiest reason path) -------------

TWIN_BODIES = "def alpha(x):\n    return x + 1\n\n\ndef beta(x):\n    return x + 1\n"
TWIN_RENAMED = "def gamma(x):\n    return x + 1\n"


def test_ambiguous_rename_reason_is_scrubbed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _write_project(tmp_path, TWIN_BODIES)
    common = ["--allow-missing-coverage", "--no-auto-cov", "--no-git"]
    runner.invoke(app, ["baseline", str(src), *common])

    # Both alpha and beta share a body fingerprint; replacing them with a single
    # identical-body gamma makes gamma match both -> AMBIGUOUS_RENAME, whose
    # reason embeds "could match: m.py::alpha, m.py::beta".
    (src / "m.py").write_text(TWIN_RENAMED, encoding="utf-8")

    redacted = runner.invoke(app, ["diff", str(src), *common, "--json", "--redact-qualnames"])
    assert redacted.exit_code == 0, redacted.output
    payload = redacted.stdout
    entries = json.loads(payload)["entries"]
    ambiguous = [e for e in entries if e["status"] == "ambiguous_rename"]
    assert ambiguous, entries
    # Neither original qualname may survive anywhere, including the reason and
    # the previous_targets list.
    assert "alpha" not in payload
    assert "beta" not in payload
    assert "alpha" not in (ambiguous[0]["reason"] or "")
    assert all("alpha" not in t["qualname"] for t in ambiguous[0]["previous_targets"])


def test_check_decision_invariant_default_gates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Invariance under *default* gates (not a tuned --fail-regression-above)."""
    monkeypatch.chdir(tmp_path)
    src = _write_project(tmp_path, "def f(x):\n    return x\n")
    common = ["--allow-missing-coverage", "--no-auto-cov", "--no-git"]
    runner.invoke(app, ["baseline", str(src), *common])

    # A large complexity jump regresses well past the default tolerance (5.0).
    big = (
        "def f(x):\n"
        + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(12))
        + "    return -1\n"
    )
    (src / "m.py").write_text(big, encoding="utf-8")

    plain = runner.invoke(app, ["check", str(src), *common, "--json"])
    redacted = runner.invoke(app, ["check", str(src), *common, "--json", "--private-comment"])
    assert plain.exit_code == redacted.exit_code == 1
    assert len(json.loads(plain.stdout)["regressions"]) == len(json.loads(redacted.stdout)["regressions"])


# --- diagnostics privacy (#7) and salt warning (#8) ----------------------


def test_diagnostics_paths_redacted_under_redaction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _write_project(tmp_path, COMPLEX)
    common = ["--allow-missing-coverage", "--no-auto-cov", "--no-git"]
    runner.invoke(app, ["baseline", str(src), *common])

    out = tmp_path / "diag.json"
    result = runner.invoke(
        app,
        ["check", str(src), *common, "--private-comment", "--debug-json-file", str(out)],
    )
    assert result.exit_code in (0, 1), result.output
    envelope = json.loads(out.read_text(encoding="utf-8"))
    baseline_path = envelope["diagnostics"]["baseline"]["path"]
    # The baseline path is hashed (12 hex chars), not the real ".riskratchet.json".
    assert baseline_path != ".riskratchet.json"
    assert ".riskratchet.json" not in baseline_path
    assert len(baseline_path) == 12
    # The always-on banner on stderr must not leak the source path either.
    assert "src/m.py" not in result.stderr
    assert str(src) not in result.stderr


def test_unsalted_redaction_warns_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # not a git repo -> no auto salt
    monkeypatch.delenv("RISKRATCHET_REDACT_SALT", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    src = _write_project(tmp_path, COMPLEX)
    result = runner.invoke(app, ["scan", str(src), "--no-auto-cov", "--no-git", "--redact-paths"])
    assert result.exit_code == 0
    assert result.stderr.count("redacting without a salt") == 1


def test_explicit_salt_silences_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    src = _write_project(tmp_path, COMPLEX)
    result = runner.invoke(
        app, ["scan", str(src), "--no-auto-cov", "--no-git", "--redact-paths", "--redact-salt", "s"]
    )
    assert result.exit_code == 0
    assert "redacting without a salt" not in result.stderr


# --- 0.3.8: the warning stream redacts too -------------------------------
#
# README promised redaction "hashes identifiers in every output format", and the 0.3.5
# note claimed the plugin no longer printed raw paths into CI logs. Neither covered the
# per-file warnings `engine.analyze` raised during a scan: they went straight to stderr
# with `print()`, so a `--private-comment` run hashed its report and named the real
# modules two lines above it.


BROKEN = "def alpha(x:\n"  # unparseable: reported as a skipped file


def _leaky_project(tmp_path: Path) -> Path:
    """A project whose scan raises both per-file disclosures: a parse failure and a
    file absent from coverage data."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "leak-fixture"\nversion = "0.0.0"\n', encoding="utf-8"
    )
    src = tmp_path / "src" / "secretpkg"
    src.mkdir(parents=True, exist_ok=True)
    (src / "acquisition_target.py").write_text(BROKEN, encoding="utf-8")
    (src / "ok.py").write_text(COMPLEX + "\n", encoding="utf-8")
    (tmp_path / "coverage.json").write_text(
        json.dumps({"meta": {"version": "7.0.0"}, "files": {}}), encoding="utf-8"
    )
    return src


def test_warnings_do_not_name_scanned_files_under_redaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _leaky_project(tmp_path)
    result = runner.invoke(
        app,
        [
            "scan",
            "src",
            "--no-git",
            "--no-auto-cov",
            "--coverage",
            "coverage.json",
            "--private-comment",
            "--redact-salt",
            "s",
        ],
    )
    assert result.exit_code == 0, result.output
    # Both warnings are still raised -- redaction must not silence a disclosure.
    assert "has no matching entry in coverage data" in result.stderr
    assert "syntax error" in result.stderr
    # ...but neither names the module.
    assert "secretpkg" not in result.stderr
    assert "acquisition_target" not in result.stderr
    assert "ok.py" not in result.stderr


def test_a_redacted_warning_carries_the_same_digest_as_its_report_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of a redacted warning is that you can still tell which function it is about.

    `redact_function_id` hashes `FunctionId.path`, which is `relative_posix(path, config_dir)`.
    A warning that hashes any other spelling -- the absolute path, say -- produces a digest
    matching no row in the report, which is a warning nobody can act on.
    """
    monkeypatch.chdir(tmp_path)
    _leaky_project(tmp_path)
    args = ["--no-git", "--no-auto-cov", "--coverage", "coverage.json", "--redact-paths"]
    result = runner.invoke(app, ["scan", "src", *args, "--redact-salt", "s", "--format", "json"])
    assert result.exit_code == 0, result.output

    expected = redact_function_id(
        FunctionId(path="src/secretpkg/ok.py", qualname="alpha"),
        RedactionConfig(redact_paths=True, salt="s"),
    ).path
    assert f"warning: {expected} has no matching entry in coverage data" in result.stderr
    # And that digest is the one the report itself prints.
    assert expected in result.stdout


def test_warnings_stay_stable_when_run_from_outside_the_config_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running from elsewhere used to print the absolute path -- the whole tree, username
    included -- because the parse-failure warning named `parsed.path`, not the root-relative
    spelling every other surface uses."""
    project = tmp_path / "project"
    project.mkdir()
    _leaky_project(project)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    result = runner.invoke(
        app,
        [
            "scan",
            str(project / "src"),
            "--config",
            str(project / "pyproject.toml"),
            "--no-git",
            "--no-auto-cov",
            "--coverage",
            str(project / "coverage.json"),
            "--private-comment",
            "--redact-salt",
            "s",
        ],
    )
    assert result.exit_code == 0, result.output
    assert str(tmp_path) not in result.stderr
    assert "secretpkg" not in result.stderr


def test_setup_errors_still_name_the_file_you_must_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The line redaction does not cross. A missing coverage report is addressed to whoever
    ran the command, and a hashed filename would make the remediation unfollowable."""
    monkeypatch.chdir(tmp_path)
    _leaky_project(tmp_path)
    result = runner.invoke(
        app,
        ["scan", "src", "--no-git", "--no-auto-cov", "--coverage", "absent.json", "--private-comment"],
    )
    assert result.exit_code == 2, result.output
    assert "absent.json" in result.stderr
