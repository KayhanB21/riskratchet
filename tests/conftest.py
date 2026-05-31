"""Test-wide fixtures.

The autouse `block_auto_coverage_runner` fixture neuters the auto-coverage
shim so test runs don't accidentally invoke a real `pytest --cov` subprocess
when they exercise the CLI without setting up coverage data. Tests that want
to verify the auto-coverage path explicitly do so by passing their own
`runner=` callable directly into `ensure_coverage`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import riskratchet.auto_coverage as auto_coverage


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
