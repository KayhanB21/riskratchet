"""Structural validation of the root `action.yml` (P27).

The composite action lets users adopt riskratchet with
`uses: KayhanB21/riskratchet@v0.2.8` instead of copy-pasting the
ci.yml pattern. These tests catch shape regressions (missing inputs,
broken step ordering, lost sticky-comment marker) without spinning up
a runner.
"""

from __future__ import annotations

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


def test_action_install_step_uses_pip() -> None:
    steps = _load()["runs"]["steps"]
    install = next((s for s in steps if s.get("name") == "Install riskratchet"), None)
    assert install is not None, "action must install riskratchet"
    run = str(install.get("run") or "")
    assert "pip install" in run
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
