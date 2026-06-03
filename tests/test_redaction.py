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
from riskratchet.redaction import RedactionConfig, redact_function_id, resolve_salt

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
    assert resolve_salt("cli-salt", "cfg-salt") == "cli-salt"
    assert resolve_salt(None, "cfg-salt") == "env-salt"
    monkeypatch.delenv("RISKRATCHET_REDACT_SALT")
    assert resolve_salt(None, "cfg-salt") == "cfg-salt"
    assert resolve_salt(None, None) is None


def test_redact_function_id_partial() -> None:
    fid = FunctionId(path="src/m.py", qualname="Foo.bar")
    paths_only = redact_function_id(fid, RedactionConfig(redact_paths=True))
    assert paths_only.path != "src/m.py"
    assert paths_only.qualname == "Foo.bar"


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
