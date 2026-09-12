"""The published schemas are reachable — from the artifact, and from their own `$id`.

Through 0.3.6 neither was true. `unzip -l` on the wheel found zero schemas, so
`pip install riskratchet` gave you the tool without its contract; and every `$id` and
`$schema` pointed at `github.com/KayhanB21/riskratchet/schemas/...`, which 404s, so a
validator that followed the URL got HTML-free nothing. The README promised both.

The packaging half has to be checked against a **built wheel**, not the source tree:
`force-include` copies files at build time only, and the dev environment is an editable
install, so `importlib.resources` here would resolve to `src/riskratchet` and report
success whether or not the build was configured at all. Opening the artifact with
`zipfile` is the only assertion that sees what an adopter receives.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from riskratchet import __version__
from riskratchet.cli import DOCTOR_SCHEMA_URL
from riskratchet.config import CONFIG_SCHEMA_URL
from riskratchet.reporting.summary import (
    DEBUG_SCHEMA_URL,
    DIFF_SCHEMA_URL,
    EXPLAIN_SCHEMA_URL,
    OUTPUT_VERSION,
    REGRESSIONS_SCHEMA_URL,
    REPORT_SCHEMA_URL,
    SUMMARY_SCHEMA_URL,
)
from riskratchet.schemas import (
    SCHEMA_BASE_URL,
    SCHEMA_NAMES,
    UnknownSchemaError,
    load_schema,
    schema_names,
    schema_path,
    schema_url,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = REPO_ROOT / "schemas"

# Every `*_SCHEMA_URL` constant in the codebase. `baseline` has none by design: the baseline
# file is riskratchet's own state, not an output document, so it carries no `$schema` field.
ALL_SCHEMA_URL_CONSTANTS = {
    "report": REPORT_SCHEMA_URL,
    "regressions": REGRESSIONS_SCHEMA_URL,
    "diff": DIFF_SCHEMA_URL,
    "summary": SUMMARY_SCHEMA_URL,
    "explain": EXPLAIN_SCHEMA_URL,
    "debug": DEBUG_SCHEMA_URL,
    "config": CONFIG_SCHEMA_URL,
    "doctor": DOCTOR_SCHEMA_URL,
}


@pytest.fixture(scope="session")
def built_artifacts(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Build the wheel and sdist once and hand back their paths."""
    if shutil.which("uv") is None:
        pytest.skip("uv is not on PATH; cannot build the artifacts to inspect")
    out = tmp_path_factory.mktemp("dist")
    result = subprocess.run(
        ["uv", "build", "--out-dir", str(out)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"uv build failed:\n{result.stdout}\n{result.stderr}")
    wheels = list(out.glob("*.whl"))
    sdists = list(out.glob("*.tar.gz"))
    assert len(wheels) == 1 and len(sdists) == 1, (wheels, sdists)
    return wheels[0], sdists[0]


def test_the_wheel_ships_every_published_schema(built_artifacts: tuple[Path, Path]) -> None:
    wheel, _ = built_artifacts
    with zipfile.ZipFile(wheel) as archive:
        shipped = {
            name.rsplit("/", 1)[-1]: archive.read(name)
            for name in archive.namelist()
            if name.startswith("riskratchet/schemas/") and name.endswith(".schema.json")
        }
    assert set(shipped) == {f"{name}.schema.json" for name in SCHEMA_NAMES}
    for filename, payload in shipped.items():
        assert payload == (SCHEMAS_DIR / filename).read_bytes(), f"{filename} differs from the repo copy"


def test_the_sdist_ships_every_published_schema(built_artifacts: tuple[Path, Path]) -> None:
    _, sdist = built_artifacts
    with tarfile.open(sdist) as archive:
        shipped = sorted(
            member.name.rsplit("/", 1)[-1]
            for member in archive.getmembers()
            if "/schemas/" in member.name and member.name.endswith(".schema.json")
        )
    assert shipped == sorted(f"{name}.schema.json" for name in SCHEMA_NAMES)


def test_the_published_set_and_the_directory_agree() -> None:
    """Both directions: a new schema file that is never registered would never ship."""
    on_disk = sorted(path.name.removesuffix(".schema.json") for path in SCHEMAS_DIR.glob("*.schema.json"))
    assert list(SCHEMA_NAMES) == on_disk
    assert schema_names() == SCHEMA_NAMES


@pytest.mark.parametrize("name", SCHEMA_NAMES)
def test_each_id_is_its_own_resolving_url(name: str) -> None:
    """The `$id` names the file it is in, under the one base URL — so following it lands here."""
    schema = json.loads((SCHEMAS_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
    assert schema["$id"] == schema_url(name)
    assert schema["$id"].rsplit("/", 1)[-1] == f"{name}.schema.json"
    assert schema["$id"].startswith(f"{SCHEMA_BASE_URL}/")


def test_no_schema_url_is_spelled_by_hand_anywhere_in_the_source() -> None:
    """The 404 host is gone, and no constant re-spells a URL `riskratchet.schemas` owns."""
    stale = [
        path
        for path in (REPO_ROOT / "src").rglob("*.py")
        if "github.com/KayhanB21/riskratchet/schemas" in path.read_text(encoding="utf-8")
    ]
    assert stale == []
    hardcoded = [
        path
        for path in (REPO_ROOT / "src").rglob("*.py")
        if path.name != "__init__.py" and SCHEMA_BASE_URL in path.read_text(encoding="utf-8")
    ]
    assert hardcoded == [], "schema URLs must come from riskratchet.schemas.schema_url"


@pytest.mark.parametrize("name", sorted(ALL_SCHEMA_URL_CONSTANTS))
def test_every_schema_url_constant_points_at_a_published_schema(name: str) -> None:
    assert ALL_SCHEMA_URL_CONSTANTS[name] == schema_url(name)


def test_every_published_schema_that_documents_an_output_has_a_constant() -> None:
    """Only `baseline` may lack one: it is riskratchet's own state file, not an output document."""
    assert set(SCHEMA_NAMES) - set(ALL_SCHEMA_URL_CONSTANTS) == {"baseline"}


def test_output_version_is_the_packages_major_minor() -> None:
    """It is derived, so it can never again sit at 0.2 across a minor bump the way it did
    from 0.2.x through 0.3.6 — including straight across 0.3.0's Breaking output change."""
    assert ".".join(__version__.split(".")[:2]) == OUTPUT_VERSION
    assert len(OUTPUT_VERSION.split(".")) == 2


def test_load_schema_returns_the_same_document_the_repo_publishes() -> None:
    for name in SCHEMA_NAMES:
        assert load_schema(name) == json.loads(
            (SCHEMAS_DIR / f"{name}.schema.json").read_text(encoding="utf-8")
        )
        assert schema_path(name).is_file()


def test_an_unknown_schema_name_is_refused_rather_than_guessed() -> None:
    with pytest.raises(UnknownSchemaError):
        schema_path("nope")
    with pytest.raises(UnknownSchemaError):
        schema_url("nope")
    with pytest.raises(UnknownSchemaError):
        load_schema("nope")
