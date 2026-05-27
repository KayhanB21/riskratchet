"""Enforce the family-submodule layering rule for `riskratchet.reporting`.

The 0.2.6 split established a strict layering inside the
`src/riskratchet/reporting/` package:

- `summary.py` is the dependency leaf; it imports nothing from any
  other reporting submodule.
- Every family submodule (`text`, `markdown`, `json_payload`, `sarif`,
  `annotations`) imports only from `summary` (plus stdlib and
  `riskratchet.models` / `riskratchet.scoring`).
- Family submodules never import from each other.
- `__init__.py` is the only file that imports from all submodules
  (re-export surface); it's exempt from the family-isolation rule.

Without enforcement, a future refactor could silently introduce a
cross-family import (e.g. `markdown.py` importing from `text.py`),
which would re-create the tangle that the split was supposed to
remove. This test parses each submodule's AST and asserts the
forbidden imports never appear.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPORTING_DIR = Path(__file__).resolve().parent.parent / "src" / "riskratchet" / "reporting"
FAMILY_MODULES = ("text", "markdown", "json_payload", "sarif", "annotations")
ALLOWED_INTERNAL_DEPENDENCY = "summary"


def _imports_in(path: Path) -> list[str]:
    """Return the dotted module names referenced by `import` / `from … import`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # Relative import: rebuild as `riskratchet.reporting.<module>`
                # so the assertion can match cleanly.
                if node.module is not None:
                    names.append(f"riskratchet.reporting.{node.module}")
                else:
                    names.append("riskratchet.reporting.__init__")
            elif node.module is not None:
                names.append(node.module)
    return names


@pytest.mark.parametrize("family", FAMILY_MODULES)
def test_family_submodule_imports_only_summary(family: str) -> None:
    """`text`/`markdown`/`json_payload`/`sarif`/`annotations` must not import
    from each other. Only `riskratchet.reporting.summary` is allowed as an
    internal dependency."""
    path = REPORTING_DIR / f"{family}.py"
    assert path.exists(), f"expected submodule {path} to exist"

    forbidden: list[str] = []
    for dotted in _imports_in(path):
        if not dotted.startswith("riskratchet.reporting"):
            continue  # stdlib + external deps are fine
        if dotted == f"riskratchet.reporting.{ALLOWED_INTERNAL_DEPENDENCY}":
            continue  # the leaf is the only permitted shared dependency
        if dotted == "riskratchet.reporting":
            # importing the package root would create a circular
            # import; flag it.
            forbidden.append(dotted)
            continue
        forbidden.append(dotted)

    assert forbidden == [], (
        f"{family}.py imports from forbidden reporting submodules: "
        f"{forbidden}. Family submodules may only import from "
        f"`riskratchet.reporting.{ALLOWED_INTERNAL_DEPENDENCY}` (the leaf)."
    )


def test_summary_imports_no_other_reporting_submodule() -> None:
    """`summary.py` is the leaf — it must not depend on any sibling."""
    path = REPORTING_DIR / "summary.py"
    assert path.exists()
    offenders = [dotted for dotted in _imports_in(path) if dotted.startswith("riskratchet.reporting")]
    assert offenders == [], (
        f"summary.py is the dependency leaf and must not import from reporting submodules: {offenders}"
    )


def test_all_family_modules_are_present() -> None:
    """Guard against a submodule going missing from the package."""
    expected = {f"{name}.py" for name in (*FAMILY_MODULES, "summary", "__init__")}
    actual = {p.name for p in REPORTING_DIR.glob("*.py")}
    assert expected.issubset(actual), (
        f"expected {expected} submodules in {REPORTING_DIR}, found {actual}. Missing: {expected - actual}"
    )
