"""Release integrity checks that should fail before a tag is cut."""

from __future__ import annotations

import sys
from importlib.metadata import metadata, version
from pathlib import Path

from typer.testing import CliRunner

from riskratchet import __version__
from riskratchet.cli import app

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

    assert expected == "0.2.3"
    assert version("riskratchet") == expected
    assert metadata("riskratchet")["Version"] == expected
    assert __version__ == expected

    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == expected


def test_installed_readme_metadata_uses_absolute_logo_url() -> None:
    readme = metadata("riskratchet")["Description"]

    assert "https://raw.githubusercontent.com/KayhanB21/riskratchet/master/assets/logo.png" in readme
    assert '<img src="assets/logo.png"' not in readme
