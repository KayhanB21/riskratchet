"""Config discovery, anchoring, and unknown-key warning (0.2.7).

These exercise the CLI end-to-end through `CliRunner` so the discovery
walk, the config-directory anchoring of `paths`, and the unknown-key
warning are all checked on the real dispatch path. `scan` is run with
`--no-git --no-auto-cov` and no coverage so output is deterministic and
no test command is spawned.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from riskratchet.cli import app

runner = CliRunner()

SRC = "def handler(value):\n    if value > 0:\n        return value\n    return -value\n"


def _write_source(path: Path, body: str = SRC) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _make_project(root: Path, *, pyproject: str) -> None:
    (root / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    _write_source(root / "src" / "m.py")


def _scan() -> list[str]:
    return ["scan", "--json", "--no-git", "--no-auto-cov"]


def test_discovery_from_nested_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Running from a nested package directory finds the ancestor config."""
    _make_project(tmp_path, pyproject='[tool.riskratchet]\npaths = ["src"]\n')
    nested = tmp_path / "pkg" / "sub"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    result = runner.invoke(app, _scan())

    assert result.exit_code == 0, result.stdout
    assert "src/m.py" in result.stdout


def test_nested_run_matches_root_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The anchoring contract: a nested invocation produces byte-identical
    output to running from the project root."""
    _make_project(tmp_path, pyproject='[tool.riskratchet]\npaths = ["src"]\n')
    nested = tmp_path / "pkg" / "sub"
    nested.mkdir(parents=True)

    monkeypatch.chdir(tmp_path)
    root_out = runner.invoke(app, _scan()).stdout

    monkeypatch.chdir(nested)
    nested_out = runner.invoke(app, _scan()).stdout

    assert root_out == nested_out
    assert "src/m.py" in root_out


def test_explicit_config_overrides_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--config` wins over the upward walk and anchors paths to its own dir."""
    _make_project(tmp_path, pyproject='[tool.riskratchet]\npaths = ["src"]\n')
    other = tmp_path / "other"
    _write_source(other / "lib" / "x.py")
    (other / "pyproject.toml").write_text('[tool.riskratchet]\npaths = ["lib"]\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["scan", "--config", str(other / "pyproject.toml"), *_scan()[1:]])

    assert result.exit_code == 0, result.stdout
    assert "lib/x.py" in result.stdout
    assert "src/m.py" not in result.stdout


def test_cli_path_stays_cwd_relative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A positional path resolves against the current directory, not the
    discovered config directory; output still anchors to the config root."""
    _make_project(tmp_path, pyproject='[tool.riskratchet]\npaths = ["src"]\n')
    pkg = tmp_path / "pkg"
    _write_source(pkg / "src" / "n.py")
    monkeypatch.chdir(pkg)

    result = runner.invoke(app, ["scan", "src", "--json", "--no-git", "--no-auto-cov"])

    assert result.exit_code == 0, result.stdout
    assert "pkg/src/n.py" in result.stdout
    assert "src/m.py" not in result.stdout


def test_no_config_silent_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No `[tool.riskratchet]` ancestor: fall back to cwd with no warning."""
    _write_source(tmp_path / "src" / "m.py")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["scan", "src", "--json", "--no-git", "--no-auto-cov"])

    assert result.exit_code == 0, result.stdout
    assert "src/m.py" in result.stdout
    assert "warning" not in result.stderr.lower()


def test_unknown_key_warns_but_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo'd key warns on stderr but the command still runs (exit 0) and
    stdout stays a clean JSON payload."""
    _make_project(tmp_path, pyproject='[tool.riskratchet]\npaths = ["src"]\nfail_new_abvoe = 1\n')
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, _scan())

    assert result.exit_code == 0, result.stdout
    assert "fail_new_abvoe" in result.stderr
    assert "warning" in result.stderr.lower()
    assert result.stdout.lstrip().startswith("{")


def test_config_validate_rejects_unknown_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`config validate` stays the strict gate: the same typo exits 2."""
    _make_project(tmp_path, pyproject='[tool.riskratchet]\npaths = ["src"]\nfail_new_abvoe = 1\n')
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["config", "validate"])

    assert result.exit_code == 2
    assert "fail_new_abvoe" in result.stderr
