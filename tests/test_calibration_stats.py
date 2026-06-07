"""Unit + parity tests for the calibration harness statistics module.

`bin/calibration/stats.py` is the canonical implementation; the frozen P24 script
(`bin/experiments/sprawl_vs_complexity.py`) carries byte-identical copies it loads
standalone. The parity tests assert the two cannot drift, so the P24 finding's
numbers stay reproducible from either entry point.
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path
from types import ModuleType

from bin.calibration import stats

EXPERIMENT_PATH = Path(__file__).resolve().parent.parent / "bin" / "experiments" / "sprawl_vs_complexity.py"


def _load_experiment() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sprawl_experiment_parity", EXPERIMENT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- canonical behavior --------------------------------------------------


def test_pearson() -> None:
    assert round(stats.pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]), 4) == 1.0
    assert round(stats.pearson([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]), 4) == -1.0
    assert math.isnan(stats.pearson([1.0], [1.0]))
    assert math.isnan(stats.pearson([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]))


def test_spearman() -> None:
    xs = [1.0, 2.0, 3.0, 4.0]
    ys = [1.0, 4.0, 9.0, 16.0]
    assert round(stats.spearman(xs, ys), 4) == 1.0
    assert stats.pearson(xs, ys) < 1.0
    assert round(stats.spearman([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]), 4) == -1.0
    assert math.isnan(stats.spearman([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]))


def test_distribution() -> None:
    dist = stats.distribution([0.0, 0.0, 10.0, 20.0, 95.0])
    assert dist["n"] == 5
    assert dist["min"] == 0.0
    assert dist["max"] == 95.0
    assert dist["zeros_frac"] == 0.4
    hist = dist["hist_0_100_by_10"]
    assert isinstance(hist, list)
    assert sum(hist) == 5
    assert hist[9] == 1
    assert stats.distribution([]) == {"n": 0}


def test_mann_whitney_u_separates_groups() -> None:
    # group_a strictly greater than group_b => U maximal, effect = +1.
    res = stats.mann_whitney_u([10.0, 11.0, 12.0], [1.0, 2.0, 3.0])
    assert res["effect"] == 1.0
    assert res["u"] == 9.0  # n_a * n_b
    # Reversed => effect = -1.
    rev = stats.mann_whitney_u([1.0, 2.0, 3.0], [10.0, 11.0, 12.0])
    assert rev["effect"] == -1.0
    # No separation (identical distributions) => effect ~ 0.
    flat = stats.mann_whitney_u([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
    assert round(flat["effect"], 4) == 0.0
    # Empty group => NaN, no crash.
    assert math.isnan(stats.mann_whitney_u([], [1.0])["u"])


# --- single source of truth ----------------------------------------------


def test_p24_experiment_reuses_canonical_stats() -> None:
    """The P24 script imports `stats.py` rather than carrying its own copy.

    Asserts it loads the *same file* (so the helpers can't drift) and that the
    re-exported private names still behave as the finding + its pinned test
    expect.
    """
    mod = _load_experiment()
    assert Path(mod._stats.__file__) == Path(stats.__file__)
    xs = [1.0, 2.0, 3.0, 4.0, 4.0, 7.0]
    ys = [2.0, 2.0, 9.0, 1.0, 5.0, 6.0]
    assert mod._pearson(xs, ys) == stats.pearson(xs, ys)
    assert mod._rank(xs) == stats.rank(xs)
    assert mod._spearman(xs, ys) == stats.spearman(xs, ys)
    assert mod._distribution([0.0, 3.2, 100.0]) == stats.distribution([0.0, 3.2, 100.0])
