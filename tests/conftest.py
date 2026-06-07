"""Test-wide fixtures.

The autouse `block_auto_coverage_runner` fixture neuters the auto-coverage
shim so test runs don't accidentally invoke a real `pytest --cov` subprocess
when they exercise the CLI without setting up coverage data. Tests that want
to verify the auto-coverage path explicitly do so by passing their own
`runner=` callable directly into `ensure_coverage`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

import riskratchet.auto_coverage as auto_coverage

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _register_bin_package() -> None:
    """Make the dev-only ``bin/`` package importable in tests.

    Registers exactly ``bin`` (via its ``__init__``) so ``from bin.calibration
    import ...`` resolves — without putting the repo root on ``sys.path``, which
    would let any test import arbitrary top-level modules and mask real import
    bugs. ``riskratchet`` itself is imported from its installed editable package,
    not through this.
    """
    if "bin" in sys.modules:
        return
    init = _REPO_ROOT / "bin" / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "bin", init, submodule_search_locations=[str(_REPO_ROOT / "bin")]
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["bin"] = module
    spec.loader.exec_module(module)


_register_bin_package()


@pytest.fixture(autouse=True)
def block_auto_coverage_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(command: str, cwd: Path) -> int:
        raise AssertionError(
            "auto_coverage._default_runner was invoked during tests "
            f"(command: {command!r}, cwd: {cwd!r}). Pass --no-auto-cov, "
            "--allow-missing-coverage, or inject a fake runner via "
            "ensure_coverage(runner=...)."
        )

    monkeypatch.setattr(auto_coverage, "_default_runner", refuse)
