"""Tests for configurable component weights.

The weights table is part of the public config surface (`[tool.riskratchet.weights]`).
These tests pin: (1) the merge-and-renormalize behavior, (2) the validation
errors, and (3) that overrides actually change the produced risk score end-to-end
via the CLI.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from typer.testing import CliRunner

from riskratchet.cli import app
from riskratchet.models import RiskComponents
from riskratchet.scoring import (
    DEFAULT_WEIGHTS,
    InvalidWeightsError,
    resolve_weights,
    total_risk,
)


def test_resolve_weights_none_returns_defaults() -> None:
    assert resolve_weights(None) == DEFAULT_WEIGHTS
    # The returned dict is a copy: mutating it must not poison the module-level default.
    resolved = resolve_weights(None)
    resolved["coverage_gap"] = 0.99
    assert DEFAULT_WEIGHTS["coverage_gap"] == 0.30


def test_resolve_weights_partial_override_renormalizes() -> None:
    resolved = resolve_weights({"churn": 0.0})
    assert math.isclose(sum(resolved.values()), 1.0)
    # churn is gone, every other component grows proportionally.
    assert resolved["churn"] == 0.0
    assert resolved["coverage_gap"] > DEFAULT_WEIGHTS["coverage_gap"]


def test_resolve_weights_arbitrary_positive_numbers_normalize() -> None:
    resolved = resolve_weights({k: 1.0 for k in DEFAULT_WEIGHTS})
    for value in resolved.values():
        assert math.isclose(value, 1.0 / 6)


def test_resolve_weights_rejects_unknown_keys() -> None:
    with pytest.raises(InvalidWeightsError, match="unknown weight keys"):
        resolve_weights({"typo_name": 0.5})


def test_resolve_weights_rejects_negative() -> None:
    with pytest.raises(InvalidWeightsError, match="non-negative"):
        resolve_weights({"churn": -1.0})


def test_resolve_weights_rejects_non_numeric() -> None:
    with pytest.raises(InvalidWeightsError, match="must be a number"):
        resolve_weights({"churn": "lots"})  # type: ignore[dict-item]


def test_resolve_weights_rejects_all_zero() -> None:
    with pytest.raises(InvalidWeightsError, match="greater than zero"):
        resolve_weights({k: 0.0 for k in DEFAULT_WEIGHTS})


def test_total_risk_honours_custom_weights() -> None:
    only_churn = resolve_weights({k: (1.0 if k == "churn" else 0.0) for k in DEFAULT_WEIGHTS})
    components = RiskComponents(
        coverage_gap=100.0,
        structural_complexity=100.0,
        branch_gap=100.0,
        churn=12.0,
        public_surface=100.0,
        sprawl=100.0,
    )
    # Everything weighted at zero except churn: score should be exactly churn.
    assert total_risk(components, weights=only_churn) == pytest.approx(12.0)


def test_cli_reads_weights_from_pyproject(tmp_path: Path) -> None:
    """End-to-end: [tool.riskratchet.weights] in config changes the score."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text(
        "def big(x):\n" + "    if x: return 1\n" * 30 + "    return 0\n",
        encoding="utf-8",
    )
    config = tmp_path / "pyproject.toml"
    # Weight everything onto churn. With --no-git, churn stats are always zero,
    # so the score must collapse to 0 regardless of complexity or coverage.
    config.write_text(
        "[tool.riskratchet]\n"
        "auto_coverage = false\n"
        "\n"
        "[tool.riskratchet.weights]\n"
        "coverage_gap = 0.0\n"
        "structural_complexity = 0.0\n"
        "branch_gap = 0.0\n"
        "churn = 1.0\n"
        "public_surface = 0.0\n"
        "sprawl = 0.0\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(src),
            "--config",
            str(config),
            "--no-git",
            "--no-auto-cov",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["functions"], "expected at least one scanned function"
    for fn in payload["functions"]:
        assert fn["score"] == 0.0


def test_cli_invalid_weight_key_exits_two(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text("def f(): return 1\n", encoding="utf-8")
    config = tmp_path / "pyproject.toml"
    config.write_text(
        "[tool.riskratchet]\nauto_coverage = false\n\n[tool.riskratchet.weights]\ntypo = 1.0\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        app,
        ["scan", str(src), "--config", str(config), "--no-git", "--no-auto-cov"],
    )

    assert result.exit_code == 2
    assert "unknown weight keys" in result.stderr or "unknown weight keys" in result.output
