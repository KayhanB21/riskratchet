"""Release integrity checks that should fail before a tag is cut."""

from __future__ import annotations

import re
import sys
from importlib.metadata import PackageNotFoundError, metadata, version
from pathlib import Path

import pytest
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

    assert expected == "0.2.13"
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
