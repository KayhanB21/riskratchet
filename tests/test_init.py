"""Tests for `riskratchet init` (P15).

Covers: starter-config write (idempotent + force), runner detection
(pytest vs unittest signals), CI snippet output, and the smoke test
against the existing `tests/fixtures/monorepo/` pyproject so the
acceptance criterion is exercised.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from typer.testing import CliRunner

from riskratchet.cli import app
from riskratchet.init import (
    InitOutcome,
    RunnerKind,
    detect_test_runner,
    render_ci_snippet,
    write_starter_config,
)

runner = CliRunner()

MONOREPO_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "monorepo" / "pyproject.toml"


def test_creates_pyproject_when_missing(tmp_path: Path) -> None:
    target = tmp_path / "pyproject.toml"
    assert not target.exists()
    outcome = write_starter_config(target, force=False)
    assert outcome is InitOutcome.CREATED
    assert "[tool.riskratchet]" in target.read_text(encoding="utf-8")


def test_appends_when_pyproject_has_no_riskratchet(tmp_path: Path) -> None:
    target = tmp_path / "pyproject.toml"
    target.write_text(
        dedent(
            """
            [project]
            name = "demo"
            version = "0.0.1"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    outcome = write_starter_config(target, force=False)
    assert outcome is InitOutcome.APPENDED
    text = target.read_text(encoding="utf-8")
    assert "[project]" in text
    assert "[tool.riskratchet]" in text


def test_skips_when_riskratchet_already_present(tmp_path: Path) -> None:
    target = tmp_path / "pyproject.toml"
    target.write_text(
        dedent(
            """
            [tool.riskratchet]
            paths = ["custom"]
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    outcome = write_starter_config(target, force=False)
    assert outcome is InitOutcome.SKIPPED
    # Custom value preserved (no overwrite).
    assert 'paths = ["custom"]' in target.read_text(encoding="utf-8")


def test_force_replaces_existing_block(tmp_path: Path) -> None:
    target = tmp_path / "pyproject.toml"
    target.write_text(
        dedent(
            """
            [project]
            name = "demo"

            [tool.riskratchet]
            paths = ["old"]
            fail_above = 99
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    outcome = write_starter_config(target, force=True)
    assert outcome is InitOutcome.REPLACED
    text = target.read_text(encoding="utf-8")
    assert "[project]" in text  # other sections preserved
    assert 'paths = ["src"]' in text  # starter values
    assert "fail_above" not in text  # old custom keys gone


def test_detect_runner_finds_pytest_from_conftest(tmp_path: Path) -> None:
    (tmp_path / "conftest.py").write_text("# pytest config\n", encoding="utf-8")
    assert detect_test_runner(tmp_path) is RunnerKind.PYTEST


def test_detect_runner_finds_pytest_from_pyproject_tool_block(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        dedent(
            """
            [tool.pytest.ini_options]
            testpaths = ["tests"]
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    assert detect_test_runner(tmp_path) is RunnerKind.PYTEST


def test_detect_runner_finds_pytest_from_project_dependencies(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        dedent(
            """
            [project]
            name = "demo"
            dependencies = ["pytest>=8.0"]
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    assert detect_test_runner(tmp_path) is RunnerKind.PYTEST


def test_detect_runner_falls_back_to_unittest(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("import unittest\n", encoding="utf-8")
    assert detect_test_runner(tmp_path) is RunnerKind.UNITTEST


def test_detect_runner_unknown_when_no_signals(tmp_path: Path) -> None:
    assert detect_test_runner(tmp_path) is RunnerKind.UNKNOWN


def test_render_ci_snippet_pins_action_to_release_tag() -> None:
    snippet = render_ci_snippet("v0.2.8")
    assert "KayhanB21/riskratchet@v0.2.8" in snippet
    assert "coverage: coverage.json" in snippet


def test_render_ci_snippet_defaults_to_action_ref_constant() -> None:
    """Decoupled from `__version__`: the default ref is the release tag, not
    the runtime package version, so pre-release builds still emit a valid
    snippet that pins the tag the release will create."""
    from riskratchet.init import ACTION_REF

    snippet = render_ci_snippet()
    assert f"KayhanB21/riskratchet@{ACTION_REF}" in snippet


def test_render_ci_snippet_sha_pins_checkout() -> None:
    """SHA-pinning `actions/checkout` defends against tag-mutation supply-chain
    attacks. The full 40-char SHA + readable v-tag in a trailing comment."""
    snippet = render_ci_snippet()
    assert "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683" in snippet
    assert "# v4.2.2" in snippet
    assert "actions/checkout@v4\n" not in snippet


def test_init_cli_creates_and_prints_snippet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert "created" in result.stdout
    assert "KayhanB21/riskratchet@v" in result.stdout
    assert "Next:" in result.stdout
    assert (tmp_path / "pyproject.toml").exists()


def test_init_cli_is_idempotent_no_force(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])  # first run: created
    result = runner.invoke(app, ["init"])  # second run: skipped
    assert result.exit_code == 0, result.output
    assert "skipped" in result.stdout
    assert "--force" in result.stdout


def test_init_cli_no_baseline_flag_prints_next_steps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--no-baseline` skips the prompt entirely, even on a TTY."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "--no-baseline"])
    assert result.exit_code == 0, result.output
    assert "Next:" in result.stdout
    assert "running: pytest --cov" not in result.stdout


def test_init_cli_skips_prompt_when_not_tty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a TTY (CliRunner.invoke default), no prompt fires and the
    `Next:` list ships as the fallback."""
    monkeypatch.chdir(tmp_path)
    # Drop a pytest signal so the runner detector returns PYTEST; the prompt
    # gate also requires TTY, which CliRunner.invoke does not simulate.
    (tmp_path / "conftest.py").write_text("", encoding="utf-8")
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, result.output
    assert "Next:" in result.stdout
    assert "Run pytest --cov and create a baseline now?" not in result.stdout


def test_init_cli_force_replaces_existing_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--force` replaces an existing block and preserves unrelated sections."""
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "pyproject.toml"
    target.write_text(
        dedent(
            """
            [project]
            name = "demo"
            version = "0.0.1"

            [tool.riskratchet]
            paths = ["legacy_src"]
            fail_new_above = 30

            [tool.ruff]
            line-length = 100
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["init", "--force", "--no-baseline"])
    assert result.exit_code == 0, result.output
    assert "replaced" in result.stdout
    text = target.read_text(encoding="utf-8")
    # Other sections preserved byte-for-byte.
    assert "[project]" in text
    assert 'name = "demo"' in text
    assert "[tool.ruff]" in text
    assert "line-length = 100" in text
    # Old riskratchet block replaced with the starter.
    assert "legacy_src" not in text
    assert 'paths = ["src"]' in text


def test_init_cli_no_snippet_suppresses_snippet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "--no-snippet"])
    assert result.exit_code == 0, result.output
    assert "KayhanB21/riskratchet" not in result.stdout


def test_init_against_monorepo_fixture_is_no_op(tmp_path: Path) -> None:
    """Acceptance: re-running `init` on a configured project (the monorepo
    fixture) is a SKIPPED no-op; the user's existing `[tool.riskratchet]`
    block (paths, coverage_map, groups) survives untouched."""
    # Copy the fixture so we don't modify the real test fixture file.
    target = tmp_path / "pyproject.toml"
    target.write_text(MONOREPO_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    outcome = write_starter_config(target, force=False)
    assert outcome is InitOutcome.SKIPPED
    text = target.read_text(encoding="utf-8")
    assert 'paths = ["packages/alpha", "packages/beta"]' in text
    assert "[tool.riskratchet.coverage_map]" in text
    assert "[tool.riskratchet.groups]" in text


def test_init_against_monorepo_fixture_force_replaces_block(tmp_path: Path) -> None:
    """Force re-init blows away the existing config (including subtables)
    and writes the starter. Documents the --force semantic."""
    target = tmp_path / "pyproject.toml"
    target.write_text(MONOREPO_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    outcome = write_starter_config(target, force=True)
    assert outcome is InitOutcome.REPLACED
    text = target.read_text(encoding="utf-8")
    assert 'paths = ["src"]' in text
    # Subtables under [tool.riskratchet.*] are intentionally removed.
    assert "[tool.riskratchet.coverage_map]" not in text
    assert "[tool.riskratchet.groups]" not in text
