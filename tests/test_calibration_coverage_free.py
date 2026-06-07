"""Coverage-free scoring: the static four components come through with no coverage file."""

from __future__ import annotations

from pathlib import Path

from bin.calibration.corpus import analyze_report
from bin.calibration.coverage_free import (
    COVERAGE_FREE_WEIGHTS,
    recompute_coverage_free_total,
)

from riskratchet.scoring import total_risk


def test_weights_drop_coverage_and_renormalize() -> None:
    assert COVERAGE_FREE_WEIGHTS["coverage_gap"] == 0.0
    assert COVERAGE_FREE_WEIGHTS["branch_gap"] == 0.0
    static = sum(
        COVERAGE_FREE_WEIGHTS[k] for k in ("structural_complexity", "churn", "public_surface", "sprawl")
    )
    assert round(static, 6) == 1.0


def test_coverage_free_total_ignores_coverage_components(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    # A branchy public function => non-zero complexity + sprawl, regardless of coverage.
    body = (
        "def normalize(record):\n"
        "    out = {}\n"
        "    if 'id' in record:\n"
        "        out['id'] = record['id']\n"
        "    if 'amount' in record:\n"
        "        out['amount'] = record['amount']\n"
        "    return out\n"
    )
    (src / "m.py").write_text(body, encoding="utf-8")

    # No coverage path => pessimistic missing coverage: coverage_gap saturates to 100.
    report = recompute_coverage_free_total(analyze_report([src], tmp_path))
    assert report.functions
    fn = next(f for f in report.functions if f.id.qualname == "normalize")

    # Static signals are present and real.
    assert fn.components.structural_complexity > 0.0
    # The cached score equals the coverage-free total, and excludes coverage_gap (weight 0)
    # even though coverage_gap itself is the pessimistic 100.
    assert fn.components.coverage_gap == 100.0
    assert round(fn.score, 6) == round(total_risk(fn.components, weights=COVERAGE_FREE_WEIGHTS), 6)
    assert fn.score < 100.0  # not dominated by the (zero-weighted) coverage gap
