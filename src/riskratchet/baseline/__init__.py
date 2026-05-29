"""Baseline I/O and regression detection.

This package replaces the single `baseline.py` module that shipped
through `0.2.6`. The public surface is preserved exactly via re-export
from the submodules:

- `io`          — baseline JSON load/save + serialization.
- `compare`     — regression classification (the `check` gate).
- `diff`        — full baseline comparison, all statuses.
- `regressions` — `DiffReport` -> failing `Regression` projection.
- `classify`    — shared matching ladder + component-regression policy.

The rename matcher itself lives in the top-level `riskratchet.matching`
module (also used by `analysis`), so it is intentionally *not* folded
into this package.

External callers should keep importing from `riskratchet.baseline`; the
submodule layout is an implementation detail.
"""

from __future__ import annotations

from riskratchet.baseline.compare import compare
from riskratchet.baseline.diff import diff
from riskratchet.baseline.io import (
    BASELINE_VERSION,
    baseline_from_report,
    load_baseline,
    save_baseline,
)
from riskratchet.baseline.regressions import regressions_from_diff

__all__ = [
    "BASELINE_VERSION",
    "baseline_from_report",
    "compare",
    "diff",
    "load_baseline",
    "regressions_from_diff",
    "save_baseline",
]
