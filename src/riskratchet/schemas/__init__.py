"""The published JSON Schemas, shipped with the package and addressable by name.

riskratchet's JSON outputs are its contract with anything that parses them, and until
0.3.7 that contract was unreachable in both directions: every `$schema` and `$id` pointed
at a URL that 404s, and `unzip -l` on the wheel found no schema at all. So a consumer could
neither fetch the contract nor read it from the package they had already installed.

Both halves are fixed here. `SCHEMA_BASE_URL` is the one place a schema URL is spelled —
the eight `*_SCHEMA_URL` constants and all nine `$id`s derive from it, so they cannot drift
apart — and the nine files ship in the wheel and the sdist beside this module.

**Where the files come from.** The repo copies under `schemas/` are the source of truth;
the build copies them in next to this module. A source checkout (or an editable install,
which is what the dev environment and the test suite use) has no copied-in set, so
`schema_path` falls back to the repo directory — the same bytes the build would have
copied. `tests/test_schema_packaging.py` opens the built wheel with `zipfile` and asserts
the shipped copies match the repo ones byte for byte, because that is the only check that
can see what an adopter actually receives.

**Versioning.** The URLs track `master`, so they always resolve to the current published
schema. Per-release pinning waits for the 1.0 schema freeze; until then, `version` inside
each document is what tells a consumer which shape they are holding.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_BASE_URL = "https://raw.githubusercontent.com/KayhanB21/riskratchet/master/schemas"

# Every schema riskratchet publishes. The names are the file stems; `test_schema_packaging.py`
# pins this tuple against the directory contents in both directions, so a new schema file that
# is never added here (and so never ships) fails the build rather than shipping half a contract.
SCHEMA_NAMES: tuple[str, ...] = (
    "baseline",
    "config",
    "debug",
    "diff",
    "doctor",
    "explain",
    "regressions",
    "report",
    "summary",
)

_PACKAGED_DIR = Path(__file__).resolve().parent
# The repo's own `schemas/` directory, reached from `src/riskratchet/schemas/__init__.py`.
# Used only when the packaged copies are absent, i.e. running from a source checkout.
_REPO_DIR = Path(__file__).resolve().parents[3] / "schemas"


class UnknownSchemaError(KeyError):
    """No published schema goes by that name."""


def schema_names() -> tuple[str, ...]:
    """The names of every published schema, sorted."""
    return SCHEMA_NAMES


def schema_url(name: str) -> str:
    """The resolving URL for a schema, the value its `$id` and every `$schema` field carries."""
    _require_known(name)
    return f"{SCHEMA_BASE_URL}/{name}.schema.json"


def schema_path(name: str) -> Path:
    """The on-disk path of a published schema: the packaged copy, else the repo's own.

    Raises `UnknownSchemaError` for a name riskratchet does not publish and `FileNotFoundError`
    when neither copy exists, rather than returning a path that does not resolve.
    """
    _require_known(name)
    filename = f"{name}.schema.json"
    packaged = _PACKAGED_DIR / filename
    if packaged.is_file():
        return packaged
    repo = _REPO_DIR / filename
    if repo.is_file():
        return repo
    raise FileNotFoundError(f"schema {name!r} is not present in this installation ({packaged})")


def load_schema(name: str) -> dict[str, Any]:
    """A published schema, parsed. The contract for validating riskratchet's own JSON output."""
    with schema_path(name).open(encoding="utf-8") as handle:
        loaded: dict[str, Any] = json.load(handle)
    return loaded


def _require_known(name: str) -> None:
    if name not in SCHEMA_NAMES:
        raise UnknownSchemaError(f"unknown schema {name!r}; riskratchet publishes {', '.join(SCHEMA_NAMES)}")
