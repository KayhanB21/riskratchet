"""Config discovery, anchoring, and unknown-key warning (0.2.7).

These exercise the CLI end-to-end through `CliRunner` so the discovery
walk, the config-directory anchoring of `paths`, the malformed-config
warning, and the unknown-key warning are all checked on the real
dispatch path. `scan` is run with `--no-git --no-auto-cov` and no
coverage so output is deterministic and no test command is spawned.

Assertions parse the JSON payload and compare the exact set of analyzed
function paths, rather than substring-matching stdout (a substring like
`src/m.py` would also match `other/src/m.py`).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import Result
from typer.testing import CliRunner

from riskratchet.cli import app

runner = CliRunner()

SRC = "def handler(value):\n    if value > 0:\n        return value\n    return -value\n"
MALFORMED_TOML = "[tool.riskratchet\npaths = \n"


def _write_source(path: Path, body: str = SRC) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _make_project(root: Path, *, pyproject: str) -> None:
    (root / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    _write_source(root / "src" / "m.py")


def _scan(*extra: str) -> list[str]:
    return ["scan", *extra, "--json", "--no-git", "--no-auto-cov"]


def _scanned_paths(result: Result) -> set[str]:
    """The exact set of repo-relative function paths in a `scan --json` run."""
    return {fn["path"] for fn in json.loads(result.stdout)["functions"]}


def test_discovery_from_nested_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Running from a nested package directory finds the ancestor config."""
    _make_project(tmp_path, pyproject='[tool.riskratchet]\npaths = ["src"]\n')
    nested = tmp_path / "pkg" / "sub"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    result = runner.invoke(app, _scan())

    assert result.exit_code == 0, result.stdout
    assert _scanned_paths(result) == {"src/m.py"}


def test_nested_run_matches_root_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The anchoring contract: a nested invocation produces byte-identical
    output to running from the project root."""
    _make_project(tmp_path, pyproject='[tool.riskratchet]\npaths = ["src"]\n')
    nested = tmp_path / "pkg" / "sub"
    nested.mkdir(parents=True)

    monkeypatch.chdir(tmp_path)
    root_result = runner.invoke(app, _scan())

    monkeypatch.chdir(nested)
    nested_result = runner.invoke(app, _scan())

    assert root_result.stdout == nested_result.stdout
    assert _scanned_paths(root_result) == {"src/m.py"}


def test_explicit_config_overrides_discovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--config` wins over the upward walk and anchors paths to its own dir."""
    _make_project(tmp_path, pyproject='[tool.riskratchet]\npaths = ["src"]\n')
    other = tmp_path / "other"
    _write_source(other / "lib" / "x.py")
    (other / "pyproject.toml").write_text('[tool.riskratchet]\npaths = ["lib"]\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, _scan("--config", str(other / "pyproject.toml")))

    assert result.exit_code == 0, result.stdout
    assert _scanned_paths(result) == {"lib/x.py"}


def test_nearest_config_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With `[tool.riskratchet]` at both repo root and a sub-package, the
    nearest one (walking up from cwd) wins."""
    _make_project(tmp_path, pyproject='[tool.riskratchet]\npaths = ["src"]\n')
    pkg = tmp_path / "pkg"
    _write_source(pkg / "lib" / "pkg_fn.py")
    (pkg / "pyproject.toml").write_text('[tool.riskratchet]\npaths = ["lib"]\n', encoding="utf-8")
    monkeypatch.chdir(pkg)

    result = runner.invoke(app, _scan())

    assert result.exit_code == 0, result.stdout
    assert _scanned_paths(result) == {"lib/pkg_fn.py"}


def test_cli_path_stays_cwd_relative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A positional path resolves against the current directory, not the
    discovered config directory; output still anchors to the config root."""
    _make_project(tmp_path, pyproject='[tool.riskratchet]\npaths = ["src"]\n')
    pkg = tmp_path / "pkg"
    _write_source(pkg / "src" / "n.py")
    monkeypatch.chdir(pkg)

    result = runner.invoke(app, _scan("src"))

    assert result.exit_code == 0, result.stdout
    assert _scanned_paths(result) == {"pkg/src/n.py"}


def test_no_arg_default_scans_cwd_not_whole_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With config discovered but no `paths` key and no positional argument,
    the implicit default scans the current directory only — not the whole
    project rooted at the config directory."""
    (tmp_path / "pyproject.toml").write_text("[tool.riskratchet]\n", encoding="utf-8")
    _write_source(tmp_path / "root_fn.py")
    sub = tmp_path / "sub"
    _write_source(sub / "deep.py")
    monkeypatch.chdir(sub)

    result = runner.invoke(app, _scan())

    assert result.exit_code == 0, result.stdout
    assert _scanned_paths(result) == {"sub/deep.py"}


def test_no_config_silent_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No `[tool.riskratchet]` ancestor: fall back to cwd with no warning."""
    _write_source(tmp_path / "src" / "m.py")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, _scan("src"))

    assert result.exit_code == 0, result.stdout
    assert _scanned_paths(result) == {"src/m.py"}
    assert "warning" not in result.stderr.lower()


def test_malformed_local_config_warns_and_uses_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken `pyproject.toml` in the cwd warns (instead of silently being
    skipped) and discovery falls back to the valid ancestor config."""
    _make_project(tmp_path, pyproject='[tool.riskratchet]\npaths = ["src"]\n')
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "pyproject.toml").write_text(MALFORMED_TOML, encoding="utf-8")
    monkeypatch.chdir(sub)

    result = runner.invoke(app, _scan())

    assert result.exit_code == 0, result.stdout
    assert "could not parse" in result.stderr
    assert _scanned_paths(result) == {"src/m.py"}


def test_malformed_config_warns_with_no_ancestor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken cwd `pyproject.toml` with no valid ancestor warns and falls
    back to an empty config rather than crashing."""
    (tmp_path / "pyproject.toml").write_text(MALFORMED_TOML, encoding="utf-8")
    _write_source(tmp_path / "src" / "m.py")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, _scan("src"))

    assert result.exit_code == 0, result.stdout
    assert "could not parse" in result.stderr
    assert _scanned_paths(result) == {"src/m.py"}


def test_unknown_key_warns_but_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo'd key warns on stderr but the command still runs (exit 0) and
    stdout stays a clean JSON payload."""
    _make_project(tmp_path, pyproject='[tool.riskratchet]\npaths = ["src"]\nfail_new_abvoe = 1\n')
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, _scan())

    assert result.exit_code == 0, result.stdout
    assert "fail_new_abvoe" in result.stderr
    assert "warning" in result.stderr.lower()
    assert _scanned_paths(result) == {"src/m.py"}


def test_config_validate_rejects_unknown_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`config validate` stays the strict gate: the same typo exits 2."""
    _make_project(tmp_path, pyproject='[tool.riskratchet]\npaths = ["src"]\nfail_new_abvoe = 1\n')
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["config", "validate"])

    assert result.exit_code == 2
    assert "fail_new_abvoe" in result.stderr
