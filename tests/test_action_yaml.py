"""Structural validation of the root `action.yml` (P27).

The composite action lets users adopt riskratchet with
`uses: KayhanB21/riskratchet@<release-tag>` instead of copy-pasting the
ci.yml pattern. These tests catch shape regressions (missing inputs,
broken step ordering, lost sticky-comment marker) without spinning up
a runner.
"""

from __future__ import annotations

import itertools
import re
from pathlib import Path
from typing import Any

import pytest
import yaml  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parent.parent
ACTION_YML = ROOT / "action.yml"


def _load() -> dict[str, Any]:
    payload: Any = yaml.safe_load(ACTION_YML.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_action_yaml_exists_and_loads() -> None:
    assert ACTION_YML.is_file(), f"composite action must live at repo root: {ACTION_YML}"
    payload = _load()
    assert payload["name"] == "riskratchet"
    assert isinstance(payload.get("description"), str)


def test_action_runs_is_composite() -> None:
    runs = _load()["runs"]
    assert runs["using"] == "composite", "action must be a composite action (runs.using: composite)"
    assert isinstance(runs.get("steps"), list) and runs["steps"], "must declare at least one step"


@pytest.mark.parametrize(
    "name,default",
    [
        ("paths", ""),
        ("coverage", ""),
        ("baseline", ".riskratchet.json"),
        ("fail-above", "60"),
        ("comment", "true"),
        ("python-version", "3.12"),
        ("riskratchet-version", ""),
        ("local-wheel", ""),
    ],
)
def test_action_declares_required_inputs(name: str, default: str) -> None:
    inputs = _load()["inputs"]
    assert name in inputs, f"action.yml must declare input {name!r}"
    block = inputs[name]
    assert isinstance(block.get("description"), str) and block["description"], (
        f"{name!r} input must have a non-empty description"
    )
    assert str(block.get("default", "")) == default, (
        f"input {name!r} default must be {default!r}, got {block.get('default')!r}"
    )


def test_action_install_step_uses_uv_tool_install() -> None:
    """Since 0.2.8 the install uses `uv tool install` via `astral-sh/setup-uv`
    rather than raw `pip install`; this is faster and consistent with the
    project's own dev environment."""
    steps = _load()["runs"]["steps"]
    setup_uv = next((s for s in steps if s.get("name") == "Set up uv"), None)
    assert setup_uv is not None, "action must include a `Set up uv` step"
    uses = str(setup_uv.get("uses") or "")
    assert uses.startswith("astral-sh/setup-uv@"), "uv setup must SHA-pin astral-sh/setup-uv"
    assert "@v" not in uses.split("#")[0], "uses must be SHA-pinned, not tag-pinned"
    install = next((s for s in steps if s.get("name") == "Install riskratchet"), None)
    assert install is not None, "action must install riskratchet"
    run = str(install.get("run") or "")
    assert "uv tool install" in run
    assert "RR_LOCAL_WHEEL" in run, "install must honour the local-wheel escape hatch"
    assert "riskratchet --version" in run, "install step should verify the CLI is on PATH"


def test_action_check_step_handles_no_baseline_mode() -> None:
    """The composite action falls back to `--fail-above` when the baseline
    file does not exist; that is the load-bearing P27/P28 integration.

    Since P8 (0.2.8) both modes use `--format pr-comment` because the
    no-baseline path now renders the regressions-only PR comment too."""
    steps = _load()["runs"]["steps"]
    check = next((s for s in steps if s.get("id") == "ratchet"), None)
    assert check is not None, "action must include the `ratchet` check step"
    run = str(check.get("run") or "")
    assert "--baseline" in run
    assert "--fail-above" in run
    assert "--format pr-comment" in run


def test_action_upsert_step_is_sticky() -> None:
    steps = _load()["runs"]["steps"]
    upsert = next((s for s in steps if s.get("name") == "Upsert PR comment"), None)
    assert upsert is not None, "action must include a sticky-comment upsert step"
    run = str(upsert.get("run") or "")
    assert "riskratchet-report" in run, "upsert must filter on the riskratchet sticky marker"
    assert "PATCH" in run, "upsert must edit existing comment in place"
    if_expr = str(upsert.get("if") or "")
    assert "pull_request" in if_expr
    assert "inputs.comment" in if_expr


def test_action_exit_step_surfaces_check_status() -> None:
    steps = _load()["runs"]["steps"]
    exit_step = next((s for s in steps if s.get("name") == "Surface riskratchet exit status"), None)
    assert exit_step is not None
    run = str(exit_step.get("run") or "")
    assert "steps.ratchet.outputs.status" in run
    assert "exit" in run


_PINNED_USES_RE = re.compile(r"^[^/]+/[^@]+@[a-f0-9]{40}\b")


def test_action_uses_entries_are_pinned_to_sha() -> None:
    """Same security posture as `.github/workflows/*`: pin third-party
    actions to a 40-char commit SHA so a tag move can't silently swap
    code that runs in users' CI."""
    steps = _load()["runs"]["steps"]
    uses_values: list[str] = [str(s["uses"]) for s in steps if s.get("uses")]
    assert uses_values, "composite action should pin at least one nested action"
    for value in uses_values:
        assert _PINNED_USES_RE.match(value), f"unpinned uses: {value!r}"


def test_action_check_step_truncates_the_comment_file() -> None:
    """`>` not `>>`: appending would stack two reports on a re-run and push the
    body toward GitHub's 65,536-char comment limit (a 422 fails the job)."""
    steps = _load()["runs"]["steps"]
    check = next((s for s in steps if s.get("id") == "ratchet"), None)
    assert check is not None
    run = check["run"]
    assert ">> riskratchet-comment.md" not in run
    assert "> riskratchet-comment.md" in run


def test_action_step_outputs_still_append() -> None:
    """Guard against over-correcting the redirect fix: $GITHUB_OUTPUT must be
    appended, or other steps' outputs are clobbered."""
    steps = _load()["runs"]["steps"]
    check = next((s for s in steps if s.get("id") == "ratchet"), None)
    assert check is not None
    assert '>> "$GITHUB_OUTPUT"' in check["run"]


# --- 0.3.6: the Action can turn TypeScript on ---------------------------------------
#
# Before this the officially shipped CI path could not enable TypeScript at all: no
# input passed `--typescript`, and the install step never installed the `[typescript]`
# extra, so a repo that turned it on from `[tool.riskratchet]` met the install hint in
# CI. The three inputs are passthroughs; the extra is installed on every branch.


@pytest.mark.parametrize("name", ["typescript", "ts-coverage", "ts-entry"])
def test_action_declares_the_typescript_inputs_with_empty_defaults(name: str) -> None:
    """Empty means "pass nothing": the CLI resolves `[tool.riskratchet] typescript`
    itself, so the Action never reads pyproject.toml."""
    inputs = _load()["inputs"]
    assert name in inputs, f"action.yml must declare input {name!r}"
    assert str(inputs[name].get("default", "")) == ""
    assert "[tool.riskratchet]" in inputs["typescript"]["description"]


def test_action_installs_the_typescript_extra_on_every_path() -> None:
    """PyPI latest, a pinned version, and the dogfood wheel all install
    `riskratchet[typescript]`; a config-driven repo must never see the install hint."""
    steps = _load()["runs"]["steps"]
    install = next(s for s in steps if s.get("name") == "Install riskratchet")
    installs = [line.strip() for line in str(install["run"]).splitlines() if "uv tool install" in line]
    assert len(installs) == 3, installs
    for line in installs:
        assert "[typescript]" in line, f"install branch without the extra: {line!r}"


def _check_step_run() -> str:
    steps = _load()["runs"]["steps"]
    check = next(s for s in steps if s.get("id") == "ratchet")
    return str(check["run"])


def test_action_check_step_forwards_the_typescript_inputs() -> None:
    run = _check_step_run()
    assert "--typescript" in run
    assert "--no-typescript" in run
    assert '--ts-coverage "$report"' in run
    assert '--ts-entry "$entry"' in run
    env = next(s for s in _load()["runs"]["steps"] if s.get("id") == "ratchet")["env"]
    assert env["RR_TYPESCRIPT"] == "${{ inputs.typescript }}"
    assert env["RR_TS_COVERAGE"] == "${{ inputs.ts-coverage }}"
    assert env["RR_TS_ENTRY"] == "${{ inputs.ts-entry }}"


def _forwarded_args(tmp_path: Path, **env: str) -> tuple[int, list[str]]:
    """Run the check step's shell against a stub `riskratchet` and return what it received.

    The step is executed as-is under bash — the same text the runner executes — with the
    binary on PATH replaced by a script that records its argv. Static greps prove the
    flags are mentioned; only running the script proves how the inputs become them.
    """
    import os
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required to execute the composite step")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "riskratchet"
    stub.write_text('#!/usr/bin/env bash\nprintf \'%s\\n\' "$@" > "$RR_ARGS_FILE"\n', encoding="utf-8")
    stub.chmod(0o755)
    args_file = tmp_path / "args.txt"
    full_env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "GITHUB_OUTPUT": str(tmp_path / "output.txt"),
        "RR_ARGS_FILE": str(args_file),
        "RR_PATHS": "",
        "RR_COVERAGE": "",
        "RR_TYPESCRIPT": "",
        "RR_TS_COVERAGE": "",
        "RR_TS_ENTRY": "",
        "RR_BASELINE": "does-not-exist.json",
        "RR_FAIL_ABOVE": "60",
        **env,
    }
    result = subprocess.run(
        [bash, "-c", _check_step_run()], cwd=tmp_path, env=full_env, capture_output=True, text=True
    )
    received = args_file.read_text(encoding="utf-8").split("\n")[:-1] if args_file.exists() else []
    return result.returncode, received


@pytest.mark.parametrize(
    "value,expected",
    [("true", ["--typescript"]), ("false", ["--no-typescript"]), ("", [])],
)
def test_the_typescript_input_becomes_the_matching_flag(
    tmp_path: Path, value: str, expected: list[str]
) -> None:
    code, received = _forwarded_args(tmp_path, RR_TYPESCRIPT=value)
    assert code == 0
    flags = [arg for arg in received if arg in ("--typescript", "--no-typescript")]
    assert flags == expected


def test_a_typescript_input_that_is_not_a_boolean_fails_the_step(tmp_path: Path) -> None:
    code, received = _forwarded_args(tmp_path, RR_TYPESCRIPT="yes")
    assert code == 1
    assert received == [], "the CLI must not run on an input the action could not interpret"


def test_space_separated_reports_become_repeated_flags(tmp_path: Path) -> None:
    code, received = _forwarded_args(
        tmp_path,
        RR_TS_COVERAGE="coverage/lcov.info packages/b/coverage/lcov.info",
        RR_TS_ENTRY="src/index.ts",
    )
    assert code == 0
    pairs = list(itertools.pairwise(received))
    assert ("--ts-coverage", "coverage/lcov.info") in pairs
    assert ("--ts-coverage", "packages/b/coverage/lcov.info") in pairs
    assert ("--ts-entry", "src/index.ts") in pairs
    assert received.count("--ts-coverage") == 2
