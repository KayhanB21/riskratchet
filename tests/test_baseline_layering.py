"""Enforce the family-submodule layering rule for `riskratchet.baseline`.

The 0.2.7 split established a strict layering inside the
`src/riskratchet/baseline/` package, mirroring the 0.2.6 `reporting/`
split:

- `io.py` and `classify.py` are the dependency leaves; they import
  nothing from any other baseline submodule.
- Every family submodule (`compare`, `diff`, `regressions`) imports
  only from the leaves (`io`, `classify`), plus stdlib and the
  cross-package modules `riskratchet.models` / `riskratchet.matching` /
  `riskratchet.groups`.
- Family submodules never import from each other.
- `__init__.py` is the only file that imports from the family
  submodules (re-export surface); it's exempt from the rule.

Without enforcement, a future refactor could silently introduce a
cross-family import (e.g. `diff.py` importing from `compare.py`), which
would re-create the tangle that the split was supposed to remove. This
test parses each submodule's AST and asserts the forbidden imports never
appear.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

BASELINE_DIR = Path(__file__).resolve().parent.parent / "src" / "riskratchet" / "baseline"
FAMILY_MODULES = ("compare", "diff", "regressions")
LEAF_MODULES = ("io", "classify")


def _imports_in(path: Path) -> list[str]:
    """Return the dotted module names referenced by `import` / `from … import`."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # Relative import: rebuild as `riskratchet.baseline.<module>`
                # so the assertion can match cleanly.
                if node.module is not None:
                    names.append(f"riskratchet.baseline.{node.module}")
                else:
                    names.append("riskratchet.baseline.__init__")
            elif node.module is not None:
                names.append(node.module)
    return names


@pytest.mark.parametrize("family", FAMILY_MODULES)
def test_family_submodule_imports_only_leaves(family: str) -> None:
    """`compare`/`diff`/`regressions` must not import from each other. Only
    the leaves (`io`, `classify`) are allowed as internal dependencies."""
    path = BASELINE_DIR / f"{family}.py"
    assert path.exists(), f"expected submodule {path} to exist"

    allowed = {f"riskratchet.baseline.{leaf}" for leaf in LEAF_MODULES}
    forbidden: list[str] = []
    for dotted in _imports_in(path):
        if not dotted.startswith("riskratchet.baseline"):
            continue  # stdlib + cross-package deps are fine
        if dotted in allowed:
            continue  # leaves are the only permitted shared dependencies
        forbidden.append(dotted)

    assert forbidden == [], (
        f"{family}.py imports from forbidden baseline submodules: "
        f"{forbidden}. Family submodules may only import from the leaves "
        f"{sorted(allowed)}."
    )


@pytest.mark.parametrize("leaf", LEAF_MODULES)
def test_leaf_imports_no_other_baseline_submodule(leaf: str) -> None:
    """`io.py` and `classify.py` are leaves — they must not depend on any
    sibling baseline submodule."""
    path = BASELINE_DIR / f"{leaf}.py"
    assert path.exists()
    offenders = [dotted for dotted in _imports_in(path) if dotted.startswith("riskratchet.baseline")]
    assert offenders == [], (
        f"{leaf}.py is a dependency leaf and must not import from baseline submodules: {offenders}"
    )


def test_all_submodules_are_present() -> None:
    """Guard against a submodule going missing from the package."""
    expected = {f"{name}.py" for name in (*FAMILY_MODULES, *LEAF_MODULES, "__init__")}
    actual = {p.name for p in BASELINE_DIR.glob("*.py")}
    assert expected.issubset(actual), (
        f"expected {expected} submodules in {BASELINE_DIR}, found {actual}. Missing: {expected - actual}"
    )
