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


# The starter for a tree with TypeScript under `src`: the key the Action, the pytest
# plugin, and every command read (since 0.3.6), plus the report hint as a comment
# because `init` does not know which runner will write it.
STARTER_BLOCK_TYPESCRIPT = (
    STARTER_BLOCK
    + """# TypeScript files were found under src. Needs `pip install 'riskratchet[typescript]'`.
typescript = true
# Istanbul/LCOV report(s), relative to this file, once your test runner writes one.
# ts_coverage = ["coverage/lcov.info"]
"""
)

# The starter scan path. Language detection looks here and nowhere else: walking the
# whole project would descend into `node_modules` and flag a Python repo's docs tooling.
_STARTER_PATH = "src"

# Fallback scan-path candidates, in order, when `src/` does not exist. Deliberately a
# bounded list and not a walk: `init` must not descend into `node_modules` (see above),
# and a walk on a large repo would pick something arbitrary. `lib` covers the other common
# convention; the project's own `[project] name` covers a flat layout (`myapp/myapp.py`);
# the depth-1 package scan covers everything else that looks like a Python package.
_FALLBACK_SCAN_DIRS = ("src", "lib")


def detect_scan_path(config_dir: Path) -> str | None:
    """The directory `init` should scaffold as `paths`, or None when nothing fits.

    Through 0.3.6 `init` wrote `paths = ["src"]` unconditionally. On a flat-layout repo
    (`myapp/`, no `src/`) that config is broken the moment it is written: the very next
    command `init` tells the user to run — `riskratchet baseline` — exits 2 with "scan
    paths ... do not exist: src". Scaffolding a path that is not there is not a default,
    it is a wrong answer, so `init` now looks before it writes.
    """
    for candidate in _FALLBACK_SCAN_DIRS:
        if (config_dir / candidate).is_dir():
            return candidate
    named = _project_name_dir(config_dir)
    if named is not None:
        return named
    packages = sorted(
        entry.name
        for entry in config_dir.iterdir()
        if entry.is_dir() and not entry.name.startswith(".") and (entry / "__init__.py").is_file()
    )
    # Exactly one obvious package is an answer; several is a guess, and `init` should ask
    # rather than pick one and quietly gate a third of the repo.
    if len(packages) == 1:
        return packages[0]
    return None


def _project_name_dir(config_dir: Path) -> str | None:
    """`[project] name` as a directory, when it exists — the flat-layout convention."""
    pyproject = config_dir / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    name = data.get("project", {}).get("name")
    if not isinstance(name, str):
        return None
    for candidate in (name, name.replace("-", "_")):
        if (config_dir / candidate).is_dir():
            return candidate
    return None


_PYTEST_COV = "pytest --cov --cov-branch --cov-report=json:coverage.json -q"
_VITEST_COV = (
    "npx vitest run --coverage --coverage.reporter=lcov  # or c8/nyc/jest: lcov.info or coverage-final.json"
)

# Action ref written into the CI snippet. Bump alongside the release tag
# (not `__version__`) — `init` is run against an installed version, but
# the snippet pins the *Action tag*, which only exists after publish.
ACTION_REF = "v0.3.6"

# SHA-pinned third-party Actions referenced by the snippet. Pinning to a
# full 40-char SHA prevents tag-mutation supply-chain attacks; the
# trailing comment names the human-readable tag for the next bump.
_CHECKOUT_PIN = "11bd71901bbe5b1630ceea73d27597364c9af683"  # v4.2.2
_CHECKOUT_TAG = "v4.2.2"
_SETUP_PYTHON_PIN = "5fda3b95a4ea91299a34e894583c3862153e4b97"  # v7.0.0
_SETUP_PYTHON_TAG = "v7.0.0"
_SETUP_NODE_PIN = "a0853c24544627f65ddf259abe73b1d18a591444"  # v5.0.0
_SETUP_NODE_TAG = "v5.0.0"
_PYTHON_VERSION = "3.12"
_NODE_VERSION = "22"


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


def write_starter_config(
    pyproject: Path, *, force: bool, typescript: bool = False, scan_path: str = _STARTER_PATH
) -> InitOutcome:
    """Write or refresh the `[tool.riskratchet]` block in `pyproject.toml`.

    Without `force`, existing configuration is preserved (no-op return
    `SKIPPED`). With `force`, the existing section is replaced in place
    via text substitution so surrounding TOML (comments, layout) stays
    intact. Other sections of `pyproject.toml` are never touched.
    `typescript` selects the starter that turns the TypeScript backend on.
    """
    block = starter_block(typescript=typescript, scan_path=scan_path)
    if not pyproject.exists():
        pyproject.write_text(block, encoding="utf-8")
        return InitOutcome.CREATED

    existing = pyproject.read_text(encoding="utf-8")
    has_section = _has_section(existing)
    if has_section and not force:
        return InitOutcome.SKIPPED
    if has_section and force:
        new_text = _replace_section(existing, block)
        pyproject.write_text(new_text, encoding="utf-8")
        return InitOutcome.REPLACED
    # Append at end with a blank line for separation.
    sep = "" if existing.endswith("\n") else "\n"
    pyproject.write_text(existing + sep + "\n" + block, encoding="utf-8")
    return InitOutcome.APPENDED


def starter_block(*, typescript: bool, scan_path: str = _STARTER_PATH) -> str:
    """The `[tool.riskratchet]` block `init` writes; the `src` rendering is unchanged."""
    block = STARTER_BLOCK_TYPESCRIPT if typescript else STARTER_BLOCK
    if scan_path == _STARTER_PATH:
        return block
    return block.replace(f'paths = ["{_STARTER_PATH}"]', f'paths = ["{scan_path}"]').replace(
        f"TypeScript files were found under {_STARTER_PATH}.",
        f"TypeScript files were found under {scan_path}.",
    )


def detect_typescript(config_dir: Path, scan_path: str = _STARTER_PATH) -> bool:
    """True when the starter scan path holds TypeScript files.

    Discovery only — `iter_typescript_files` imports without the `[typescript]` extra,
    so a Python-only install can still recognise a TypeScript tree and say what to
    install. The extra is needed to *score* it, which is why the starter says so.
    """
    from riskratchet.typescript import iter_typescript_files

    starter = config_dir / scan_path
    if not starter.is_dir():
        return False
    return bool(iter_typescript_files([starter], root=config_dir))


def detect_python(config_dir: Path, scan_path: str = _STARTER_PATH) -> bool:
    """True when the starter scan path holds Python files."""
    from riskratchet.analysis import iter_python_files

    starter = config_dir / scan_path
    if not starter.is_dir():
        return False
    return bool(iter_python_files([starter], root=config_dir))


def next_steps(*, typescript: bool, python: bool, scan_path: str = _STARTER_PATH) -> list[str]:
    """The "Next:" list `init` prints when it did not run the baseline itself.

    Python-only trees (and empty ones) get the same three lines as before 0.3.6. A tree
    with TypeScript gets the install, the report each runner writes, and a baseline /
    check pair that names both reports — only the ones its languages need.
    """
    if not typescript:
        return [
            _PYTEST_COV,
            f"riskratchet baseline {scan_path} --coverage coverage.json",
            f"riskratchet check {scan_path} --coverage coverage.json",
        ]
    reports = " --ts-coverage coverage/lcov.info"
    if python:
        reports = " --coverage coverage.json" + reports
    steps = ["pip install 'riskratchet[typescript]'"]
    if python:
        steps.append(_PYTEST_COV)
    steps.append(_VITEST_COV)
    steps.append(f"riskratchet baseline {scan_path}{reports}")
    steps.append(f"riskratchet check {scan_path}{reports}")
    return steps


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


def _coverage_run_step(runner: RunnerKind) -> list[str]:
    """The lines that actually produce `coverage.json`, keyed by the detected runner.

    This exists because the snippet shipped through 0.3.6 had *no* coverage step at all:
    the action was handed `coverage: coverage.json`, a file nothing in the workflow wrote,
    and a named-but-missing report is exit 2 by design. Dropping the input instead is also
    exit 2, because auto-coverage shells out to `pytest`, which is not on the PATH of the
    `uv tool install riskratchet` environment the action creates. So the workflow has to
    write the report itself, and `init` has to say so in the runner the project uses —
    printing `pytest --cov` to a unittest project would just move the failure.
    """
    if runner is RunnerKind.UNITTEST:
        return [
            "- run: |",
            "    coverage run -m unittest discover",
            "    coverage json -o coverage.json",
        ]
    step = [f"- run: {_PYTEST_COV}"]
    if runner is RunnerKind.UNKNOWN:
        step.insert(0, "# No test runner detected; this assumes pytest — swap in your own")
        step.insert(1, "# command, as long as it writes coverage.json.")
    return step


def render_ci_snippet(
    ref: str = ACTION_REF,
    *,
    runner: RunnerKind = RunnerKind.PYTEST,
    typescript: bool = False,
) -> str:
    """Return the CI snippet for the P27 composite action.

    `ref` defaults to `ACTION_REF` (the release tag), not the runtime
    `__version__`, so a user running `init` on an unreleased build
    still gets a snippet pinning the tag that will exist at release.

    `runner` and `typescript` come from the same detection `next_steps` uses, so the
    snippet and the manual steps never prescribe different commands.
    """
    lines = [
        "# Add this to .github/workflows/riskratchet.yml:",
        f"- uses: actions/checkout@{_CHECKOUT_PIN}  # {_CHECKOUT_TAG}",
        "  with:",
        "    # Full history: churn uses `git log --since`, which on the default",
        "    # shallow (depth-1) clone sees only HEAD and silently scores every",
        "    # function's churn as zero — so CI would disagree with your baseline.",
        "    fetch-depth: 0",
        f"- uses: actions/setup-python@{_SETUP_PYTHON_PIN}  # {_SETUP_PYTHON_TAG}",
        "  with:",
        f"    python-version: '{_PYTHON_VERSION}'",
        "# Install your project and its test dependencies however you normally do:",
        "- run: pip install -e '.[dev]'",
    ]
    lines += _coverage_run_step(runner)
    if typescript:
        lines += [
            f"- uses: actions/setup-node@{_SETUP_NODE_PIN}  # {_SETUP_NODE_TAG}",
            "  with:",
            f"    node-version: '{_NODE_VERSION}'",
            "- run: npm ci",
            f"- run: {_VITEST_COV}",
        ]
    lines += [
        f"- uses: KayhanB21/riskratchet@{ref}",
        "  with:",
        "    coverage: coverage.json",
    ]
    if typescript:
        lines += [
            "    typescript: 'true'",
            "    ts-coverage: coverage/lcov.info",
        ]
    return "\n".join(lines) + "\n"


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
