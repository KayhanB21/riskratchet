"""check / diff over TypeScript, routed through pipeline.build_report (B5, 0.3.0).

check and diff gain --typescript and analyze TS alongside Python. The comparison machinery
(baseline.compare / diff / match_rename) is already language-neutral, so a scored TS function flows
through the ratchet exactly like a Python one — flagged new, gated by --fail-new-above, and diffed.
(TS entries in the *baseline* itself land in B6; here the baseline is Python-only, so TS reads new.)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from riskratchet.cli import _ts_rebaseline_command, app

runner = CliRunner()


def _project(tmp_path: Path) -> Path:
    (tmp_path / "m.py").write_text("def f(x):\n    return x\n", encoding="utf-8")
    (tmp_path / "s.ts").write_text(
        "export function g(a: number) { return a > 0 ? a : -a; }\n", encoding="utf-8"
    )
    return tmp_path


def _baseline(tmp_path: Path) -> Path:
    out = tmp_path / ".rr.json"
    result = runner.invoke(
        app,
        [
            "baseline",
            str(tmp_path),
            "--output",
            str(out),
            "--no-git",
            "--no-auto-cov",
            "--allow-missing-coverage",
        ],
    )
    assert result.exit_code == 0, result.stderr
    return out


def test_diff_typescript_reports_new_ts_function(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    _project(tmp_path)
    baseline = _baseline(tmp_path)  # Python-only baseline (baseline --typescript lands in B6)
    result = runner.invoke(
        app,
        [
            "diff",
            str(tmp_path),
            "--typescript",
            "--baseline",
            str(baseline),
            "--no-git",
            "--no-auto-cov",
            "--allow-missing-coverage",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stderr
    entries: dict[str, Any] = {e["qualname"]: e for e in json.loads(result.stdout)["entries"]}
    assert entries["g"]["status"] == "new"  # the TS function, absent from the Python baseline
    assert entries["f"]["status"] == "unchanged"  # the Python function


def test_check_typescript_new_function_is_gated(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    _project(tmp_path)
    baseline = _baseline(tmp_path)
    # g scores ~41; --fail-new-above 10 makes the new TS function fail the gate → exit 1.
    result = runner.invoke(
        app,
        [
            "check",
            str(tmp_path),
            "--typescript",
            "--baseline",
            str(baseline),
            "--no-git",
            "--no-auto-cov",
            "--allow-missing-coverage",
            "--fail-new-above",
            "10",
        ],
    )
    assert result.exit_code == 1
    assert "g" in result.stdout or "g" in result.stderr


def test_baseline_typescript_writes_v3_with_identity(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    _project(tmp_path)
    out = tmp_path / ".rr.json"
    result = runner.invoke(
        app,
        [
            "baseline",
            str(tmp_path),
            "--typescript",
            "--output",
            str(out),
            "--no-git",
            "--no-auto-cov",
            "--allow-missing-coverage",
        ],
    )
    assert result.exit_code == 0, result.stderr
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert raw["version"] == "3"
    assert raw["identity"]["typescript"]["scheme"] == 2
    by_name = {e["qualname"]: e for e in raw["entries"]}
    assert by_name["g"]["language"] == "typescript"
    assert "language" not in by_name["f"]  # Python omitted


def test_check_warns_and_id_matches_on_stale_grammar(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    _project(tmp_path)
    out = tmp_path / ".rr.json"
    assert (
        runner.invoke(
            app,
            [
                "baseline",
                str(tmp_path),
                "--typescript",
                "--output",
                str(out),
                "--no-git",
                "--no-auto-cov",
                "--allow-missing-coverage",
            ],
        ).exit_code
        == 0
    )
    # Corrupt the recorded grammar so it can't match the runtime → stale.
    data = json.loads(out.read_text(encoding="utf-8"))
    data["identity"]["typescript"]["grammar"] = "0.0.1-stale"
    out.write_text(json.dumps(data), encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "check",
            str(tmp_path),
            "--typescript",
            "--baseline",
            str(out),
            "--no-git",
            "--no-auto-cov",
            "--allow-missing-coverage",
        ],
    )
    assert result.exit_code == 0
    assert "re-baseline recommended" in result.stderr
    # Assisted re-baseline: the exact regen command is printed, copy-pasteable as-is.
    assert f"re-baseline: riskratchet baseline {tmp_path} --typescript --output {out}" in result.stderr


def test_ts_rebaseline_command_renders_paths_coverage_and_output() -> None:
    # No coverage: bare `--typescript --output`.
    assert (
        _ts_rebaseline_command(
            [Path("src"), Path("app")],
            baseline_file=Path(".riskratchet.json"),
            ts_coverage=None,
        )
        == "riskratchet baseline src app --typescript --output .riskratchet.json"
    )
    # With coverage: each `--ts-coverage` report is threaded through (loop-body branch).
    assert (
        _ts_rebaseline_command(
            [Path("src")],
            baseline_file=Path("b.json"),
            ts_coverage=[Path("c8.info"), Path("nyc.json")],
        )
        == "riskratchet baseline src --typescript --ts-coverage c8.info --ts-coverage nyc.json --output b.json"
    )
    # Empty path list falls back to a placeholder rather than an unrunnable command.
    assert (
        _ts_rebaseline_command([], baseline_file=Path("b.json"), ts_coverage=None)
        == "riskratchet baseline <paths> --typescript --output b.json"
    )


def test_check_without_typescript_ignores_ts(tmp_path: Path) -> None:
    # Without --typescript, the TS file is invisible to check (Python-only), so the gate passes.
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    _project(tmp_path)
    baseline = _baseline(tmp_path)
    result = runner.invoke(
        app,
        [
            "check",
            str(tmp_path),
            "--baseline",
            str(baseline),
            "--no-git",
            "--no-auto-cov",
            "--allow-missing-coverage",
            "--fail-new-above",
            "10",
        ],
    )
    assert result.exit_code == 0, result.stderr


# --- 0.3.5: a run must not silently unratchet a language it did not analyze ---------
#
# `_refuse_to_erase_baseline` only asked whether the *whole* report was empty, so a
# `baseline` run without `--typescript` over a mixed baseline sailed past it: the Python
# half kept the report non-empty while every TypeScript entry was dropped, and it
# reported "wrote baseline with 1 functions". The read side was worse — `compare` has no
# "removed" concept, so those functions simply vanished from the comparison and `check`
# gated the Python half alone and called it clean.


def _mixed_baseline(tmp_path: Path) -> Path:
    """A baseline holding both languages, written the way a real project would."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    _project(tmp_path)
    out = tmp_path / ".rr.json"
    result = runner.invoke(
        app,
        [
            "baseline",
            str(tmp_path),
            "--typescript",
            "--output",
            str(out),
            "--no-git",
            "--no-auto-cov",
            "--allow-missing-coverage",
        ],
    )
    assert result.exit_code == 0, result.stderr
    languages = {e.get("language", "python") for e in json.loads(out.read_text())["entries"]}
    assert languages == {"python", "typescript"}, languages
    return out


def test_baseline_refuses_to_drop_every_typescript_entry(tmp_path: Path) -> None:
    """A gap in 0.3.4's own fix: the unit that must not vanish is a language, not the file."""
    out = _mixed_baseline(tmp_path)
    before = out.read_bytes()

    result = runner.invoke(
        app,
        [
            "baseline",
            str(tmp_path),
            "--output",
            str(out),  # note: no --typescript
            "--no-git",
            "--no-auto-cov",
            "--allow-missing-coverage",
        ],
    )

    assert result.exit_code == 2, result.output
    assert "analyzed no typescript functions" in result.stderr
    assert out.read_bytes() == before, "the baseline must be left byte-identical"


def test_check_says_when_it_is_gating_only_half_the_repo(tmp_path: Path) -> None:
    """Those entries used to vanish from the comparison with no mention at all."""
    out = _mixed_baseline(tmp_path)

    result = runner.invoke(
        app,
        [
            "check",
            str(tmp_path),
            "--baseline",
            str(out),  # note: no --typescript
            "--no-git",
            "--no-auto-cov",
            "--allow-missing-coverage",
        ],
    )

    assert "not being gated" in result.stderr
    assert "--typescript" in result.stderr


def test_writing_a_separate_baseline_is_still_allowed(tmp_path: Path) -> None:
    """The refusal is about *erasing* entries, not about Python-only baselines."""
    _mixed_baseline(tmp_path)

    result = runner.invoke(
        app,
        [
            "baseline",
            str(tmp_path),
            "--output",
            str(tmp_path / "python-only.json"),
            "--no-git",
            "--no-auto-cov",
            "--allow-missing-coverage",
        ],
    )

    assert result.exit_code == 0, result.output
