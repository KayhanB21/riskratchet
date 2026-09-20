"""Release integrity checks that should fail before a tag is cut."""

from __future__ import annotations

import re
import sys
from importlib.metadata import PackageNotFoundError, metadata, version
from pathlib import Path
from typing import cast

import pytest
import yaml  # type: ignore[import-untyped]
from typer.testing import CliRunner

from riskratchet import __version__
from riskratchet import _version as version_mod
from riskratchet.cli import app
from riskratchet.init import ACTION_REF

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[import-not-found]


runner = CliRunner()


def _project_version() -> str:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    version_value = project["version"]
    assert isinstance(version_value, str)
    return version_value


def test_installed_metadata_and_runtime_versions_match_pyproject() -> None:
    expected = _project_version()

    assert expected == "0.3.8"
    assert version("riskratchet") == expected
    assert metadata("riskratchet")["Version"] == expected
    assert __version__ == expected

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == expected


def test_action_ref_tracks_release_version() -> None:
    # `init`'s CI snippet pins `KayhanB21/riskratchet@{ACTION_REF}`; the tag only exists
    # after publish, so ACTION_REF must equal the version being released. This guard is
    # why ACTION_REF can't silently drift (it sat at v0.2.8 for four releases before).
    assert f"v{_project_version()}" == ACTION_REF


def test_readme_release_pins_match_version() -> None:
    # README documents two release-tag pins a user copies: the Action `uses:` and the
    # pre-commit `rev:`. Both must point at the current version, not a stale tag (the
    # README sat at v0.2.8). Scoped to riskratchet's own pins so other repos' `rev:`
    # lines in the examples don't false-positive.
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    expected = f"v{_project_version()}"
    pins = re.findall(r"KayhanB21/riskratchet@(v\d+\.\d+\.\d+)", readme)
    pins += re.findall(r"KayhanB21/riskratchet\s*\n\s*rev:\s*(v\d+\.\d+\.\d+)", readme)
    assert pins, "expected at least one riskratchet release-tag pin in README"
    stale = sorted(p for p in set(pins) if p != expected)
    assert not stale, f"stale README release pins {stale}; expected {expected}"


def test_installed_readme_metadata_uses_absolute_logo_url() -> None:
    readme = metadata("riskratchet")["Description"]

    assert "https://raw.githubusercontent.com/KayhanB21/riskratchet/master/assets/logo.png" in readme
    assert '<img src="assets/logo.png"' not in readme


def test_source_tree_version_fallback_reads_pyproject() -> None:
    assert version_mod._local_pyproject_version() == _project_version()


def test_package_version_falls_back_to_pyproject(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_metadata(_: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr(version_mod, "version", missing_metadata)

    assert version_mod.package_version() == _project_version()


_CANONICAL_WORKFLOW_HEADER = "# .github/workflows/riskratchet.yml\n"


def _canonical_readme_steps() -> list[object]:
    """The `steps:` list of the README's canonical workflow block.

    Extracted structurally rather than by substring, because the README holds more than
    one workflow block and a substring check cannot tell them apart.
    """
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    blocks = [
        block.split("```", 1)[0]
        for block in readme.split("```yaml\n")[1:]
        if block.startswith(_CANONICAL_WORKFLOW_HEADER)
    ]
    assert len(blocks) == 1, f"expected exactly one canonical workflow block, found {len(blocks)}"
    workflow = yaml.safe_load(blocks[0])
    steps = workflow["jobs"]["riskratchet"]["steps"]
    assert isinstance(steps, list)
    return cast("list[object]", steps)


def test_readme_ci_snippet_matches_render_ci_snippet() -> None:
    """The README's copy-paste workflow and `riskratchet init`'s snippet are the two
    things an adopter actually pastes into CI, and they must be the *same* steps.

    They have drifted twice. Before 0.3.2 both shipped without `fetch-depth: 0`, silently
    zeroing churn. Through 0.3.6 both shipped with no step that writes `coverage.json` at
    all, so the documented workflow exited 2 as pasted — and the check that was supposed
    to catch that only asserted each rendered line appeared *somewhere* in a 62 KB README,
    in any order, with no reverse direction. A second, broken workflow block passed it.

    Compare the parsed step lists instead: same steps, same order, both directions.
    """
    from riskratchet.init import render_ci_snippet

    rendered = yaml.safe_load(render_ci_snippet())
    assert rendered == _canonical_readme_steps()


def test_the_documented_workflow_writes_the_coverage_it_names() -> None:
    """The workflow must produce the report it hands to the action.

    `coverage: coverage.json` names a path, and a named path that does not exist is exit 2
    (AGENTS.md: "a path the user named must exist"). Dropping the input is exit 2 too,
    because auto-coverage shells out to `pytest`, which is absent from the action's
    `uv tool install` environment. So some step before the action must write the file —
    this asserts it, in both the README block and `init`'s snippet.
    """
    from riskratchet.init import render_ci_snippet

    for label, steps in (
        ("README", _canonical_readme_steps()),
        ("init", yaml.safe_load(render_ci_snippet())),
    ):
        named = [
            step["with"]["coverage"]
            for step in steps
            if isinstance(step, dict) and "riskratchet@" in str(step.get("uses", ""))
        ]
        assert named == ["coverage.json"], f"{label}: unexpected coverage input {named!r}"
        writers = [
            step for step in steps if isinstance(step, dict) and "coverage.json" in str(step.get("run", ""))
        ]
        assert writers, f"{label}: no step writes coverage.json before the action runs"
