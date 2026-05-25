"""Package version helpers."""

from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised on the 3.10 CI job.
    import tomli as tomllib  # type: ignore[import-not-found]


def _local_pyproject_version() -> str:
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if not pyproject.exists():
        return "0+unknown"

    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    project = data.get("project", {})
    value = project.get("version")
    if isinstance(value, str) and value:
        return value
    return "0+unknown"


def package_version() -> str:
    try:
        return version("riskratchet")
    except PackageNotFoundError:
        return _local_pyproject_version()


__version__ = package_version()
