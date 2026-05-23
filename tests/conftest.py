"""Shared fixtures for riskratchet tests."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest


@pytest.fixture
def sample_module(tmp_path: Path) -> Path:
    """A small module with a handful of functions of varying shapes."""
    source = dedent(
        '''
        """A sample module used in tests."""

        TOP_LEVEL_CONST = 1


        def add(a: int, b: int) -> int:
            return a + b


        def classify(value: int) -> str:
            if value < 0:
                return "negative"
            if value == 0:
                return "zero"
            if value < 10:
                return "small"
            return "large"


        def _private_helper(value: int) -> int:
            return value * 2


        class Calculator:
            def __init__(self) -> None:
                self.total = 0

            def add(self, value: int) -> None:
                self.total += value

            def _internal(self) -> int:
                return self.total

            async def async_method(self, x: int) -> int:
                return x + 1


        def outer(x: int) -> int:
            def inner(y: int) -> int:
                return y * 2

            return inner(x)
        '''
    ).strip() + "\n"
    path = tmp_path / "sample.py"
    path.write_text(source, encoding="utf-8")
    return path


@pytest.fixture
def sample_module_with_coverage(tmp_path: Path, sample_module: Path) -> tuple[Path, Path]:
    """A sample module plus a matching coverage.json."""
    # The line ranges below are computed from `sample_module`'s source above.
    coverage_payload = {
        "files": {
            "sample.py": {
                "executed_lines": [6, 7, 10, 11, 12, 21, 22, 26, 27, 28, 30, 31],
                "missing_lines": [13, 14, 15, 16, 17, 18, 33, 34, 36, 37, 40, 41, 42, 43],
                "executed_branches": [[11, 12]],
                "missing_branches": [[11, 13]],
            }
        }
    }
    coverage_path = tmp_path / "coverage.json"
    coverage_path.write_text(json.dumps(coverage_payload), encoding="utf-8")
    return sample_module, coverage_path
