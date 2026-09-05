"""Tests for `--help` rendering.

Typer renders help through Rich, which parses `[...]` as a style tag and
silently drops it. That quietly broke the two things a new user reads first:
`[tool.riskratchet]` vanished ("Falls back to  paths if omitted.") and the
TypeScript hint rendered as `pip install 'riskratchet'` — a *wrong*,
copy-pasteable command. Escaping with `\\[` is the fix; these tests pin it,
plus the invariant that no option ships without help text.
"""

from __future__ import annotations

import click
import pytest
from typer.main import get_command
from typer.testing import CliRunner

from riskratchet.cli import VALID_FORMATS, app

runner = CliRunner()

ANALYSIS_COMMANDS = ("scan", "check", "diff", "baseline")


def _help(*args: str) -> str:
    result = runner.invoke(app, [*args, "--help"])
    assert result.exit_code == 0, result.output
    return result.output


def _params(command: str) -> list[click.Parameter]:
    group = get_command(app)
    assert isinstance(group, click.Group)
    sub = group.get_command(click.Context(group), command)
    assert sub is not None, f"no such command: {command}"
    return list(sub.params)


@pytest.mark.parametrize("command", ANALYSIS_COMMANDS)
def test_paths_help_renders_the_config_table_name(command: str) -> None:
    out = " ".join(_help(command).split())

    assert "[tool.riskratchet] paths" in out
    # The exact broken rendering, and a leaked escape character.
    assert "Falls back to paths" not in out
    assert "\\[" not in out


@pytest.mark.parametrize("command", (*ANALYSIS_COMMANDS, "explain"))
def test_typescript_help_shows_the_real_install_command(command: str) -> None:
    """`pip install 'riskratchet'` would not install the TypeScript backend."""
    out = " ".join(_help(command).split())

    assert "riskratchet[typescript]" in out


@pytest.mark.parametrize("command", ("check", "diff"))
def test_format_help_lists_every_valid_format(command: str) -> None:
    """Generated from `VALID_FORMATS`, so a new format cannot go undocumented."""
    out = " ".join(_help(command).split())

    for value in VALID_FORMATS:
        assert value in out, f"--format help omits {value!r}"


@pytest.mark.parametrize("command", ("config validate", "init"))
def test_command_docstrings_render_the_config_table_name(command: str) -> None:
    out = " ".join(_help(*command.split()).split())

    assert "[tool.riskratchet]" in out
    assert "`` " not in out


@pytest.mark.parametrize("command", (*ANALYSIS_COMMANDS, "explain", "doctor", "init"))
def test_every_option_has_help_text(command: str) -> None:
    """Fails the *next* undocumented flag, not just the twelve fixed in 0.3.2."""
    undocumented = [
        param.opts
        for param in _params(command)
        if isinstance(param, click.Option) and param.name != "help" and not param.help
    ]

    assert not undocumented, f"{command}: options without help text: {undocumented}"
