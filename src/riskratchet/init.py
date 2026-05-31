"""`riskratchet init`: scaffold a starter `[tool.riskratchet]` config + CI snippet.

Idempotent: re-running on a configured project is a no-op unless `--force`.
The scaffolded config is deliberately minimal (one `paths` entry); the user
is expected to grow it as they adopt more features. The CI snippet points
at the P27 composite action so adopters get from "no riskratchet" to
"riskratchet in CI" in two paste operations.

Lives outside `cli.py` so the command stays a thin shell and the
text-manipulation logic is reusable from tests.
"""

from __future__ import annotations

import sys
from enum import Enum
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[import-not-found]

STARTER_BLOCK = """[tool.riskratchet]
# Paths to scan. Edit to match your package layout.
paths = ["src"]
"""

# Action ref written into the CI snippet. Bump alongside the release tag
# (not `__version__`) — `init` is run against an installed version, but
# the snippet pins the *Action tag*, which only exists after publish.
ACTION_REF = "v0.2.8"

# SHA-pinned third-party Actions referenced by the snippet. Pinning to a
# full 40-char SHA prevents tag-mutation supply-chain attacks; the
# trailing comment names the human-readable tag for the next bump.
_CHECKOUT_PIN = "11bd71901bbe5b1630ceea73d27597364c9af683"  # v4.2.2
_CHECKOUT_TAG = "v4.2.2"


class InitOutcome(str, Enum):
    CREATED = "created"  # pyproject.toml did not exist; created with starter block
    APPENDED = "appended"  # pyproject.toml existed; [tool.riskratchet] appended
    REPLACED = "replaced"  # --force overwrote an existing [tool.riskratchet] section
    SKIPPED = "skipped"  # [tool.riskratchet] already present; no-op without --force


class RunnerKind(str, Enum):
    """Test-runner detected by `detect_test_runner`. Named `RunnerKind`
    rather than `TestRunner` so pytest does not try to collect it as a
    test class."""

    PYTEST = "pytest"
    UNITTEST = "unittest"
    UNKNOWN = "unknown"


def write_starter_config(pyproject: Path, *, force: bool) -> InitOutcome:
    """Write or refresh the `[tool.riskratchet]` block in `pyproject.toml`.

    Without `force`, existing configuration is preserved (no-op return
    `SKIPPED`). With `force`, the existing section is replaced in place
    via text substitution so surrounding TOML (comments, layout) stays
    intact. Other sections of `pyproject.toml` are never touched.
    """
    if not pyproject.exists():
        pyproject.write_text(STARTER_BLOCK, encoding="utf-8")
        return InitOutcome.CREATED

    existing = pyproject.read_text(encoding="utf-8")
    has_section = _has_section(existing)
    if has_section and not force:
        return InitOutcome.SKIPPED
    if has_section and force:
        new_text = _replace_section(existing, STARTER_BLOCK)
        pyproject.write_text(new_text, encoding="utf-8")
        return InitOutcome.REPLACED
    # Append at end with a blank line for separation.
    sep = "" if existing.endswith("\n") else "\n"
    pyproject.write_text(existing + sep + "\n" + STARTER_BLOCK, encoding="utf-8")
    return InitOutcome.APPENDED


def detect_test_runner(config_dir: Path) -> RunnerKind:
    """Best-effort runner detection: prefer pytest signals over unittest.

    We check for several pytest fingerprints (a `pytest.ini`, a
    `[tool.pytest.ini_options]` table, a `conftest.py`, pytest listed in
    dependencies) and fall back to `unittest` when only a `tests/`
    directory with `test_*.py` files exists. Unknown when neither.
    """
    if _is_pytest(config_dir):
        return RunnerKind.PYTEST
    tests_dir = config_dir / "tests"
    if tests_dir.is_dir() and any(tests_dir.glob("test_*.py")):
        return RunnerKind.UNITTEST
    return RunnerKind.UNKNOWN


def render_ci_snippet(ref: str = ACTION_REF) -> str:
    """Return the two-step CI snippet for the P27 composite action.

    `ref` defaults to `ACTION_REF` (the release tag), not the runtime
    `__version__`, so a user running `init` on an unreleased build
    still gets a snippet pinning the tag that will exist at release.
    """
    return (
        "# Add this to .github/workflows/riskratchet.yml:\n"
        f"- uses: actions/checkout@{_CHECKOUT_PIN}  # {_CHECKOUT_TAG}\n"
        f"- uses: KayhanB21/riskratchet@{ref}\n"
        "  with:\n"
        "    coverage: coverage.json\n"
    )


def _has_section(text: str) -> bool:
    return any(line.lstrip().startswith("[tool.riskratchet]") for line in text.splitlines())


def _replace_section(existing: str, new_block: str) -> str:
    """Replace the `[tool.riskratchet]` block (and any `[tool.riskratchet.*]`
    subtables) with `new_block`, leaving every other section intact.

    Operates line-by-line on the file text so values containing `[` (TOML
    array literals) do not interfere. Subtables are deliberately dropped
    as part of the `--force` semantic ("blow away the old block and
    start over").
    """
    lines = existing.splitlines(keepends=True)
    start_idx: int | None = None
    end_idx: int | None = None
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        is_rr_table = stripped.startswith("[tool.riskratchet]") or stripped.startswith("[tool.riskratchet.")
        if start_idx is None and is_rr_table:
            start_idx = i
            continue
        if start_idx is not None and stripped.startswith("[") and not is_rr_table:
            end_idx = i
            break
    if start_idx is None:
        return existing
    if end_idx is None:
        end_idx = len(lines)
    new_block_normalized = new_block if new_block.endswith("\n") else new_block + "\n"
    return "".join(lines[:start_idx]) + new_block_normalized + "".join(lines[end_idx:])


def _is_pytest(config_dir: Path) -> bool:
    if (config_dir / "pytest.ini").exists():
        return True
    if (config_dir / "conftest.py").exists():
        return True
    pyproject = config_dir / "pyproject.toml"
    if not pyproject.exists():
        return False
    try:
        raw = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return False
    tool = raw.get("tool")
    if isinstance(tool, dict) and "pytest" in tool:
        return True
    return _mentions_pytest(raw)


def _mentions_pytest(raw: dict[str, object]) -> bool:
    project = raw.get("project")
    if isinstance(project, dict):
        deps = project.get("dependencies")
        if isinstance(deps, list) and any(isinstance(d, str) and d.startswith("pytest") for d in deps):
            return True
        opt = project.get("optional-dependencies")
        if isinstance(opt, dict):
            for items in opt.values():
                if isinstance(items, list) and any(
                    isinstance(d, str) and d.startswith("pytest") for d in items
                ):
                    return True
    groups = raw.get("dependency-groups")
    if isinstance(groups, dict):
        for items in groups.values():
            if isinstance(items, list) and any(isinstance(d, str) and d.startswith("pytest") for d in items):
                return True
    return False
