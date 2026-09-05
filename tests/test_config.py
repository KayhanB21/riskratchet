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
from riskratchet.config import (
    _BOOL_KEYS,
    _NUMBER_KEYS,
    _STRING_LIST_KEYS,
    CONFIG_ALLOWED_KEYS,
    invalid_config_values,
    unknown_config_keys,
)

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


# --- 0.3.4: the collectors behind both the warning and the exit-2 path ---------


def test_valid_config_collects_no_problems() -> None:
    cfg = {
        "paths": ["src"],
        "exclude": ["tests/**"],
        "baseline": ".riskratchet.json",
        "fail_new_above": 50,
        "fail_above": 80.5,
        "auto_coverage": True,
        "churn_window_days": 90,
        "missing_coverage": "pessimistic",
        "weights": {"churn": 0.5},
        "groups": {"api": "src/api"},
        "coverage_map": {"src": "coverage.json"},
        "typescript": True,
        "ts_coverage": ["coverage/lcov.info"],
        "ts_entry": ["src/index.ts"],
    }
    assert unknown_config_keys(cfg) == []
    assert invalid_config_values(cfg) == []


@pytest.mark.parametrize(
    ("cfg", "expected"),
    [
        ({"paths": "src"}, "paths must be a list of strings."),
        ({"exclude": [1]}, "exclude must be a list of strings."),
        ({"baseline": 3}, "baseline must be a string."),
        ({"fail_new_above": "50"}, "fail_new_above must be a number."),
        ({"fail_new_above": True}, "fail_new_above must be a number."),
        ({"auto_coverage": "yes"}, "auto_coverage must be a boolean."),
        ({"fail_above": 0}, "fail_above must be a number in (0, 100]."),
        ({"churn_window_days": 0}, "churn_window_days must be an integer >= 1."),
        ({"missing_coverage": "bogus"}, "missing_coverage must be one of"),
        ({"weights": "heavy"}, "[tool.riskratchet.weights] must be a table."),
        ({"groups": "api"}, "[tool.riskratchet.groups] must be a table."),
        ({"coverage_map": []}, "[tool.riskratchet.coverage_map] must be a table"),
        ({"typescript": "yes"}, "typescript must be a boolean."),
        ({"ts_coverage": "coverage/lcov.info"}, "ts_coverage must be a list of strings."),
        ({"ts_entry": [1]}, "ts_entry must be a list of strings."),
    ],
)
def test_invalid_config_values_collects_each_kind(cfg: dict[str, object], expected: str) -> None:
    problems = invalid_config_values(cfg)
    assert problems, f"{cfg} was accepted"
    assert any(expected in problem for problem in problems)


def test_invalid_config_values_never_raises_on_a_wrong_typed_bound() -> None:
    """`0 < "50" <= 100` is a TypeError, not a validation message.

    The range and enum rules used to run only after the type rules had *raised*,
    so collecting instead of raising put them in front of values they had never
    seen. This is the case that would crash the warning path.
    """
    assert invalid_config_values({"fail_above": "50"}) == ["fail_above must be a number."]


def test_invalid_config_values_reports_every_problem() -> None:
    problems = invalid_config_values({"paths": "src", "fail_new_above": "1", "auto_coverage": 1})
    assert len(problems) == 3


def test_every_type_checked_key_is_an_allowed_key() -> None:
    """A key validated but not allowed (or vice versa) is a silently dead rule."""
    assert set(_NUMBER_KEYS) <= CONFIG_ALLOWED_KEYS
    assert set(_BOOL_KEYS) <= CONFIG_ALLOWED_KEYS
    assert set(_STRING_LIST_KEYS) <= CONFIG_ALLOWED_KEYS


def test_config_show_still_reports_a_bad_weights_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every entry point rejects a bad weights table, by whichever route.

    `config show` reaches it through `_load_config_strict`; the analysis
    commands now reach it through `_enforce_config_or_exit`. Pinning both keeps
    the two routes from drifting to different exit codes.
    """
    _make_project(
        tmp_path,
        pyproject='[tool.riskratchet]\npaths = ["src"]\n\n[tool.riskratchet.weights]\nchurn = -1.0\n',
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["config", "show"])

    assert result.exit_code == 2, result.output
    assert "must be non-negative" in result.stderr


def test_analysis_commands_report_the_same_weights_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_project(
        tmp_path,
        pyproject='[tool.riskratchet]\npaths = ["src"]\n\n[tool.riskratchet.weights]\nchurn = -1.0\n',
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["scan", "--no-git", "--no-auto-cov"])

    assert result.exit_code == 2, result.output
    assert "must be non-negative" in result.stderr


# --- 0.3.6: `typescript`, `ts_coverage`, `ts_entry` are config keys ------------

TS_SRC = (
    "export function handler(value: number): number {\n"
    "  if (value > 0) {\n"
    "    return value;\n"
    "  }\n"
    "  return -value;\n"
    "}\n"
)
TS_PROJECT = '[tool.riskratchet]\npaths = ["src"]\ntypescript = true\n'


def _make_mixed_project(root: Path, *, pyproject: str = TS_PROJECT) -> None:
    _make_project(root, pyproject=pyproject)
    _write_source(root / "src" / "m.ts", TS_SRC)


def test_typescript_keys_are_known_config_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The shipped CI path could not enable TypeScript: the key did not exist.

    `typescript = true` warned `ignoring unknown key(s)` and scanned Python only, so an
    adopter had no way to turn the backend on from the config every other setting
    lives in — and the Action had no input to pass the flag either.
    """
    _make_mixed_project(
        tmp_path,
        pyproject=TS_PROJECT + 'ts_coverage = ["coverage/lcov.info"]\nts_entry = ["src/index.ts"]\n',
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, _scan("--no-typescript"))

    assert result.exit_code == 0, result.output
    assert "unknown" not in result.stderr
    assert runner.invoke(app, ["config", "validate"]).exit_code == 0


def test_config_typescript_scans_the_same_functions_as_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`typescript = true` and `--typescript` are one switch, not two."""
    pytest.importorskip("tree_sitter_typescript")
    _make_mixed_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    from_config = runner.invoke(app, _scan())
    assert from_config.exit_code == 0, from_config.output

    (tmp_path / "pyproject.toml").write_text('[tool.riskratchet]\npaths = ["src"]\n', encoding="utf-8")
    from_flag = runner.invoke(app, _scan("--typescript"))
    assert from_flag.exit_code == 0, from_flag.output

    assert _scanned_paths(from_config) == {"src/m.py", "src/m.ts"}
    assert json.loads(from_config.stdout)["functions"] == json.loads(from_flag.stdout)["functions"]


def test_no_typescript_beats_a_config_that_turns_it_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A switch config can turn on must be one a flag can turn back off.

    A `--typescript` flag whose default is a real `False` cannot be told apart from
    the user asking for Python-only, which is the 0.3.5 pytest-plugin lesson: config
    could never lose. `--no-typescript` is the explicit off.
    """
    _make_mixed_project(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, _scan("--no-typescript"))

    assert result.exit_code == 0, result.output
    assert _scanned_paths(result) == {"src/m.py"}


def test_no_typescript_beats_the_deprecated_alias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_mixed_project(tmp_path, pyproject='[tool.riskratchet]\npaths = ["src"]\n')
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, _scan("--experimental-typescript", "--no-typescript"))

    assert result.exit_code == 0, result.output
    assert _scanned_paths(result) == {"src/m.py"}
    assert "deprecated" not in result.stderr


def test_config_ts_coverage_anchors_to_the_config_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `ts_coverage` entry resolves against the config file, like `coverage` does.

    Observed through the missing-report error: run from a nested directory, the path
    it names is the config-anchored one, not a cwd-relative reading that would look for
    the report under the nested directory.
    """
    _make_mixed_project(tmp_path, pyproject=TS_PROJECT + 'ts_coverage = ["cov/lcov.info"]\n')
    monkeypatch.chdir(tmp_path)
    seeded = runner.invoke(
        app, ["baseline", "src", "--no-git", "--no-auto-cov", "--allow-missing-coverage", "--no-typescript"]
    )
    assert seeded.exit_code == 0, seeded.output
    nested = tmp_path / "src" / "nested"
    nested.mkdir()
    monkeypatch.chdir(nested)

    result = runner.invoke(app, ["check", "--no-git", "--no-auto-cov"])

    assert result.exit_code == 2, result.output
    assert str(tmp_path / "cov" / "lcov.info") in result.stderr
    assert "ts_coverage" in result.stderr


def test_inert_typescript_keys_do_not_fail_a_python_only_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--no-typescript` must not exit 2 on a report the run will never read."""
    _make_mixed_project(tmp_path, pyproject=TS_PROJECT + 'ts_coverage = ["cov/lcov.info"]\n')
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, _scan("--no-typescript"))

    assert result.exit_code == 0, result.output
    assert "not found" not in result.stderr
    assert "have no effect" in result.stderr


def _documented_config_keys() -> set[str]:
    """Every backticked name in the first column of the README's configuration table."""
    import re

    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Configuration reference", 1)[1].split("\n## ", 1)[0]
    keys: set[str] = set()
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        first_cell = line.split("|")[1]
        keys.update(re.findall(r"`([a-z_]+)`", first_cell))
    return keys


def test_every_allowed_config_key_is_documented_in_the_readme() -> None:
    """Adding a key is a four-place change; the README table is the fourth.

    `CONFIG_ALLOWED_KEYS`, the type rule, and the schema were all wired for the
    redaction keys while the table stayed silent for a release; this closes that gap
    for the next key.
    """
    assert _documented_config_keys() >= CONFIG_ALLOWED_KEYS
