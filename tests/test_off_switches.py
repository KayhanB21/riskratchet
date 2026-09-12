"""A switch config can turn on must be one a flag can turn back off (0.3.7).

AGENTS.md:149 states the invariant; 0.3.6 added it and four settings never satisfied it.
`[tool.riskratchet] redact_paths = true` — or `redact_qualnames`, `private_comment`,
`allow_missing_coverage` — could not be overridden for a single run, because
the helper behind them read a CLI value only when it differed from the default, so a
flag passed as its own default was indistinguishable from silence. 0.3.7 splits that into
`_config_bool` (config-only, which is all any caller actually wanted) and
`_resolved_tristate` (asked on / asked off / did not say).

Nothing here moves a score: the flags change what a run *discloses* and whether it
refuses to run, never what anything is worth.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from riskratchet.cli import app
from riskratchet.config import resolve_redaction

runner = CliRunner()

_SOURCE = """\
def widget(flag):
    if flag:
        return 1
    return 0
"""


def _project(tmp_path: Path, *, extra: str = "") -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text(_SOURCE, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0"\n\n[tool.riskratchet]\npaths = ["src"]\n' + extra,
        encoding="utf-8",
    )
    return tmp_path


def _scan(project: Path, monkeypatch: pytest.MonkeyPatch, *extra: str) -> dict[str, Any]:
    monkeypatch.chdir(project)
    result = runner.invoke(app, ["scan", "--json", "--no-auto-cov", "--no-git", *extra])
    assert result.exit_code == 0, (result.stdout, result.stderr)
    payload: dict[str, Any] = json.loads(result.stdout)
    return payload


def test_config_redaction_can_be_turned_off_for_one_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path, extra="redact_paths = true\nredact_qualnames = true\n")
    redacted = _scan(project, monkeypatch)["functions"][0]
    assert redacted["path"] != "src/m.py"  # config is in force
    assert redacted["qualname"] != "widget"

    shown = _scan(project, monkeypatch, "--no-redact-paths", "--no-redact-qualnames")["functions"][0]
    assert shown["path"] == "src/m.py"
    assert shown["qualname"] == "widget"


def test_each_off_switch_turns_off_only_its_own_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The three redaction flags compose; none is a master switch for the other two."""
    project = _project(tmp_path, extra="redact_paths = true\nredact_qualnames = true\n")
    only_paths = _scan(project, monkeypatch, "--no-redact-paths")["functions"][0]
    assert only_paths["path"] == "src/m.py"
    assert only_paths["qualname"] != "widget"

    only_names = _scan(project, monkeypatch, "--no-redact-qualnames")["functions"][0]
    assert only_names["path"] != "src/m.py"
    assert only_names["qualname"] == "widget"


def test_no_private_comment_turns_off_the_preset_not_the_keys_underneath(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`private_comment` is a preset; turning it off leaves an explicit `redact_paths` alone."""
    project = _project(tmp_path, extra="private_comment = true\nredact_paths = true\n")
    out = _scan(project, monkeypatch, "--no-private-comment")["functions"][0]
    assert out["qualname"] == "widget"  # the preset's qualname half is gone
    assert out["path"] != "src/m.py"  # the key the user set on its own still applies


def test_an_explicit_off_flag_beats_an_active_preset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Otherwise `private_comment = true` would leave a setting no flag can turn off."""
    project = _project(tmp_path, extra="private_comment = true\n")
    out = _scan(project, monkeypatch, "--no-redact-paths")["functions"][0]
    assert out["path"] == "src/m.py"
    assert out["qualname"] != "widget"  # the rest of the preset is untouched


def test_off_beats_on_when_both_flags_are_passed(tmp_path: Path) -> None:
    """A wrapper appending `--no-x` to a command line it did not write has to win.

    The same precedence `_resolve_typescript_flag` uses for `--no-typescript`.
    """
    redaction = resolve_redaction(
        redact_paths=True,
        redact_qualnames=False,
        private_comment=False,
        redact_salt="s",
        cfg={},
        config_dir=tmp_path,
        no_redact_paths=True,
    )
    assert redaction.redact_paths is False


def test_no_allow_missing_coverage_requires_the_coverage_config_allowed_to_be_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _project(tmp_path, extra="allow_missing_coverage = true\n")
    monkeypatch.chdir(project)
    assert runner.invoke(app, ["baseline", "--no-auto-cov", "--no-git"]).exit_code == 0
    assert runner.invoke(app, ["check", "--no-auto-cov", "--no-git"]).exit_code == 0

    refused = runner.invoke(app, ["check", "--no-auto-cov", "--no-git", "--no-allow-missing-coverage"])
    assert refused.exit_code == 2, (refused.stdout, refused.stderr)


@pytest.mark.parametrize(
    ("command", "flags"),
    [
        ("scan", ["--no-redact-paths", "--no-redact-qualnames", "--no-private-comment"]),
        ("check", ["--no-redact-paths", "--no-redact-qualnames", "--no-private-comment"]),
        ("explain", ["--no-redact-paths", "--no-redact-qualnames", "--no-private-comment"]),
        ("diff", ["--no-redact-paths", "--no-redact-qualnames", "--no-private-comment"]),
        ("baseline", ["--no-allow-missing-coverage"]),
        ("check", ["--no-allow-missing-coverage"]),
        ("diff", ["--no-allow-missing-coverage"]),
    ],
)
def test_every_command_that_carries_the_on_switch_carries_the_off_switch(
    command: str, flags: list[str]
) -> None:
    """The invariant is per command, not per codebase: a flag missing from one door is the bug."""
    help_text = runner.invoke(app, [command, "--help"]).stdout
    for flag in flags:
        assert flag in help_text, f"{command} is missing {flag}"
