"""Guards for the P24 sprawl investigation.

These lock in the two facts the finding (`docs/sprawl-component-finding.md`)
rests on, so a future scoring change can't silently invalidate the writeup:

1. The sprawl file-line term moves an otherwise-identical function's score,
   while `structural_complexity` stays put.
2. The experiment script's helpers behave as documented.
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path
from types import ModuleType

from riskratchet.models import ChurnStats, ComplexityStats, CoverageStats, FileStats, FunctionSpan
from riskratchet.scoring import DEFAULT_WEIGHTS, compute_components, total_risk

EXPERIMENT_PATH = Path(__file__).resolve().parent.parent / "bin" / "experiments" / "sprawl_vs_complexity.py"


def _load_experiment() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sprawl_experiment", EXPERIMENT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _score_in_file(total_lines: int) -> tuple[float, float]:
    span = FunctionSpan(start_line=1, end_line=40)
    comp = compute_components(
        is_public=True,
        span=span,
        complexity=ComplexityStats(cyclomatic=8),
        coverage=CoverageStats(line_coverage=0.5, branch_coverage=0.5),
        churn=ChurnStats(commits=0),
        file_stats=FileStats(path="m.py", total_lines=total_lines, function_count=10),
    )
    return total_risk(comp, weights=DEFAULT_WEIGHTS), comp.structural_complexity


def test_file_size_moves_score_but_not_structural_complexity() -> None:
    small_score, small_struct = _score_in_file(300)
    big_score, big_struct = _score_in_file(1200)
    # The file-line term lifts the score of a byte-identical function...
    assert big_score > small_score
    # ...by the full sprawl swing: weight 0.10 * half of sprawl * 100 = 5.0.
    assert round(big_score - small_score, 2) == 5.0
    # ...while structural_complexity is unaffected by file size.
    assert small_struct == big_struct


def test_experiment_synthetic_grid_is_monotonic() -> None:
    mod = _load_experiment()
    grid = mod.synthetic_grid()
    rows = grid["rows"]
    scores = [r["total_score"] for r in rows]
    structural = [r["structural_complexity"] for r in rows]
    assert scores == sorted(scores)  # score rises with file size
    assert len(set(structural)) == 1  # structural complexity is constant


def test_experiment_pearson_helper() -> None:
    mod = _load_experiment()
    assert round(mod._pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]), 4) == 1.0
    assert round(mod._pearson([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]), 4) == -1.0


def test_experiment_spearman_helper() -> None:
    mod = _load_experiment()
    # Perfectly monotonic (but non-linear) → Spearman 1.0 where Pearson < 1.
    xs = [1.0, 2.0, 3.0, 4.0]
    ys = [1.0, 4.0, 9.0, 16.0]
    assert round(mod._spearman(xs, ys), 4) == 1.0
    assert mod._pearson(xs, ys) < 1.0
    assert round(mod._spearman([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]), 4) == -1.0
    # Ties get averaged ranks, so a flat series correlates with nothing.
    assert math.isnan(mod._spearman([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]))


def test_experiment_distribution_helper() -> None:
    mod = _load_experiment()
    dist = mod._distribution([0.0, 0.0, 10.0, 20.0, 95.0])
    assert dist["n"] == 5
    assert dist["min"] == 0.0
    assert dist["max"] == 95.0
    assert dist["zeros_frac"] == 0.4
    assert sum(dist["hist_0_100_by_10"]) == 5
    assert dist["hist_0_100_by_10"][9] == 1  # the 95.0 lands in the top bucket
    assert mod._distribution([]) == {"n": 0}
