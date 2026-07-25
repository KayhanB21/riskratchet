"""Guards for the TS complexity-calibration derivation (B2, 0.3.0).

These pin the endpoint-percentile math and tie the shipped
`scoring.TYPESCRIPT_COMPLEXITY_CALIBRATION` to the committed derivation artifact, so the constant
can't drift away from the data it claims to come from. The corpus study itself is human-run (it
clones repos); only the pure derivation logic is exercised here.
"""

from __future__ import annotations

import json
from pathlib import Path

from bin.calibration.ts_complexity_calibration import (
    OUTPUT,
    _fraction_leq,
    _quantile,
    derive,
)

from riskratchet.scoring import TYPESCRIPT_COMPLEXITY_CALIBRATION


def test_quantile_matches_numpy_linear_method() -> None:
    xs = [float(v) for v in range(1, 101)]  # 1..100
    assert _quantile(xs, 0.0) == 1.0
    assert _quantile(xs, 1.0) == 100.0
    # pos = 0.5 * 99 = 49.5 -> interpolate sorted[49]=50 and sorted[50]=51
    assert _quantile(xs, 0.50) == 50.5
    # pos = 0.99 * 99 = 98.01 -> sorted[98]=99, sorted[99]=100
    assert round(_quantile(xs, 0.99), 3) == 99.01


def test_fraction_leq() -> None:
    values = [1, 1, 1, 2, 5, 21, 30]
    assert _fraction_leq(values, 1) == 3 / 7
    assert round(_fraction_leq(values, 21), 4) == round(6 / 7, 4)


def test_derive_matches_python_endpoints_at_same_percentiles() -> None:
    # Python: 42% at CC=1, up to 99% below 21 -> p_free=0.42, p_sat=0.99.
    python = [1] * 42 + [10] * 57 + [30] * 1
    ts = [float(v) for v in range(1, 101)]  # uniform 1..100
    result = derive(python, [int(v) for v in ts])
    assert result["method"] == "endpoint-percentile-match"
    anchors = result["anchor_percentiles"]
    assert isinstance(anchors, dict)
    assert anchors["p_free"] == 0.42
    assert anchors["p_sat"] == 0.99
    # free_ts = quantile(ts, 0.42), sat_ts = quantile(ts, 0.99), then rounded with free>=1, sat>free.
    band = result["typescript_calibration"]
    assert isinstance(band, list)
    free_ts, sat_ts = band[0], band[1]
    assert free_ts == max(1, round(_quantile(ts, 0.42)))
    assert sat_ts == max(free_ts + 1, round(_quantile(ts, 0.99)))


def test_shipped_constant_matches_committed_artifact() -> None:
    # The scoring constant must equal the recorded derivation output, so a re-run that changes the
    # number forces the committed JSON (and this assertion) to move together.
    artifact = json.loads(Path(OUTPUT).read_text(encoding="utf-8"))
    free, saturation = TYPESCRIPT_COMPLEXITY_CALIBRATION
    assert [free, saturation] == artifact["typescript_calibration"]
