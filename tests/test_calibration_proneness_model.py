"""The structure-beats-activity null-vs-full model (scipy; skips without the group)."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("scipy")

import numpy as np
from bin.calibration.ablation import Dataset
from bin.calibration.proneness_model import FEATURES, run_proneness, to_payload

_N = len(FEATURES)
_PAST_CHURN_COL = 0
_COMPLEXITY_COL = 1
_FILE_LINE_COL = 3


def _dataset(signal_col: int | None, *, n_repos: int = 4, per_repo: int = 80, seed: int = 0) -> Dataset:
    rng = np.random.default_rng(seed)
    xs, ys, rs = [], [], []
    for repo in range(n_repos):
        n_prone = per_repo // 4  # 25% change-prone
        labels = [1.0] * n_prone + [0.0] * (per_repo - n_prone)
        for lab in labels:
            row = rng.normal(size=_N)
            if lab == 1.0 and signal_col is not None:
                row[signal_col] += 2.5
            xs.append(row)
            ys.append(lab)
            rs.append(repo)
    return Dataset(
        repos=tuple(f"r{i}" for i in range(n_repos)),
        repo_index=np.asarray(rs, dtype=int),
        x=np.vstack(xs),
        y=np.asarray(ys, dtype=float),
    )


def _tiers(ds: Dataset) -> dict[str, str]:
    return {r: "polished" for r in ds.repos}


def test_structure_beats_activity_when_structure_carries_the_signal() -> None:
    # Signal lives in a structural feature; past-churn (the null) is noise.
    ds = _dataset(signal_col=_COMPLEXITY_COL, seed=1)
    result = run_proneness(ds, _tiers(ds), bootstrap_draws=40)

    full = float(np.mean([a for _, _, a in result.full_per_repo]))
    null = float(np.mean([a for _, _, a in result.null_per_repo]))
    assert full > null  # structure adds predictive value over activity
    assert "beat the past-churn null" in to_payload(result)["verdict"]  # type: ignore[operator]


def test_structure_adds_nothing_when_activity_is_the_signal() -> None:
    # Signal lives only in past-churn; the null model already captures it.
    ds = _dataset(signal_col=_PAST_CHURN_COL, seed=2)
    result = run_proneness(ds, _tiers(ds), bootstrap_draws=40)

    full = float(np.mean([a for _, _, a in result.full_per_repo]))
    null = float(np.mean([a for _, _, a in result.null_per_repo]))
    assert null > 0.6  # the null model sees the activity signal
    assert full - null < 0.05  # structure adds little
    # The file-line half is noise here, so its CI straddles zero.
    lo, hi = result.file_line_ci
    assert lo <= 0.0 <= hi


def test_payload_shape_and_determinism() -> None:
    ds = _dataset(signal_col=_COMPLEXITY_COL, seed=3)
    a = json.dumps(to_payload(run_proneness(ds, _tiers(ds), bootstrap_draws=25)), sort_keys=True)
    b = json.dumps(to_payload(run_proneness(ds, _tiers(ds), bootstrap_draws=25)), sort_keys=True)
    assert a == b

    payload = json.loads(a)
    assert payload["model"]["features_null"] == ["past_churn"]
    assert payload["model"]["features_full"] == list(FEATURES)
    assert set(payload["gradient"]) == {"polished", "messy"}
    assert "structure_beats_activity" in payload
