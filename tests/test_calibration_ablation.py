"""Tests for the pooled, repo-stratified logistic-regression ablation (phase 3).

scipy/numpy live in the optional `calibration` dependency group, so this whole module
skips when they are absent (default dev/CI run, or any interpreter without a wheel).
Run it with: `uv run --group calibration pytest tests/test_calibration_ablation.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("scipy")
pytest.importorskip("numpy")

import numpy as np
from bin.calibration.ablation import (
    CONTINUOUS_PREDICTORS,
    FILE_LINE_INDEX,
    Dataset,
    build_dataset,
    cross_val_loro,
    run_ablation,
    to_payload,
)
from bin.calibration.config import REPOS_DIR
from bin.calibration.corpus import CALIBRATION_DIR, analyze_report
from bin.calibration.defects import SnapshotPopulation

from riskratchet.models import FunctionId

_N_PREDICTORS = len(CONTINUOUS_PREDICTORS)


def _dataset(
    *,
    n_repos: int = 4,
    per_repo: int = 60,
    buggy_per_repo: int | list[int] = 12,
    signal_col: int | None = 0,
    seed: int = 0,
) -> Dataset:
    """Synthetic pooled dataset. Buggy rows are shifted up on `signal_col` (None = no signal)."""
    rng = np.random.default_rng(seed)
    buggy_counts = [buggy_per_repo] * n_repos if isinstance(buggy_per_repo, int) else buggy_per_repo
    xs: list[np.ndarray] = []
    ys: list[float] = []
    rs: list[int] = []
    for repo in range(n_repos):
        n_buggy = buggy_counts[repo]
        labels = [1.0] * n_buggy + [0.0] * (per_repo - n_buggy)
        for lab in labels:
            row = rng.normal(size=_N_PREDICTORS)
            if lab == 1.0 and signal_col is not None:
                row[signal_col] += 3.0
            xs.append(row)
            ys.append(lab)
            rs.append(repo)
    return Dataset(
        repos=tuple(f"r{i}" for i in range(n_repos)),
        repo_index=np.asarray(rs, dtype=int),
        x=np.vstack(xs),
        y=np.asarray(ys, dtype=float),
    )


def test_build_dataset_extracts_features_and_labels(tmp_path: Path) -> None:
    root = tmp_path / "snap"
    src = root / "src"
    src.mkdir(parents=True)
    body = "def f{n}(x):\n    return x + {n}\n"
    for i in range(4):
        (src / f"m{i}.py").write_text(body.format(n=i), encoding="utf-8")
    report = analyze_report([src], root)
    _ = SnapshotPopulation(snapshot_sha="S" * 40, report=report)
    defect_ids = {FunctionId("src/m0.py", "f0")}

    dataset = build_dataset([("demo", report, defect_ids)])

    assert dataset.repos == ("demo",)
    assert dataset.x.shape == (len(report.functions), _N_PREDICTORS)
    assert dataset.n_buggy == 1
    assert np.isfinite(dataset.x).all()
    # The single positive row corresponds to the labelled function.
    buggy_rows = np.where(dataset.y == 1.0)[0]
    assert buggy_rows.shape == (1,)


def test_model_recovers_separating_predictor() -> None:
    # File-line term carries the only signal: keeping it must help, drop must hurt.
    dataset = _dataset(signal_col=FILE_LINE_INDEX, seed=1)
    result = run_ablation(dataset, bootstrap_draws=40)

    full_aucs = [a for _, _, a in result.full_per_repo]
    assert full_aucs and min(full_aucs) > 0.5  # signal recovered in every held-out repo
    # Full (with the file-line term) beats drop, and the coefficient is positively signed
    # with a CI that excludes zero.
    full_mean = float(np.mean(full_aucs))
    drop_mean = float(np.mean([a for _, _, a in result.drop_per_repo]))
    assert full_mean > drop_mean
    assert result.file_line_coef > 0.0
    lo, _ = result.file_line_ci
    assert lo > 0.0
    assert "independent positive signal" in to_payload(result)["verdict"]  # type: ignore[operator]


def test_drop_file_line_on_noise_does_not_hurt() -> None:
    # Signal is in a non-file-line predictor; the file-line term is pure noise.
    dataset = _dataset(signal_col=0, seed=2)
    result = run_ablation(dataset, bootstrap_draws=60)

    full_mean = float(np.mean([a for _, _, a in result.full_per_repo]))
    drop_mean = float(np.mean([a for _, _, a in result.drop_per_repo]))
    # Dropping a noise feature must not meaningfully reduce cross-validated AUC.
    assert drop_mean >= full_mean - 0.02
    # And the file-line coefficient's CI should straddle zero.
    lo, hi = result.file_line_ci
    assert lo <= 0.0 <= hi
    assert "no defensible independent signal" in to_payload(result)["verdict"]  # type: ignore[operator]


def test_run_ablation_is_deterministic() -> None:
    dataset = _dataset(signal_col=0, seed=3)
    a = json.dumps(to_payload(run_ablation(dataset, bootstrap_draws=30)), sort_keys=True)
    b = json.dumps(to_payload(run_ablation(dataset, bootstrap_draws=30)), sort_keys=True)
    assert a == b


def test_zero_buggy_repo_is_dropped_from_cv() -> None:
    # r0 has no buggy functions: its within-repo AUC is undefined, so it must be
    # absent from the CV results but still present in the dataset (and no crash).
    dataset = _dataset(n_repos=3, buggy_per_repo=[0, 12, 12], signal_col=0, seed=4)
    per_repo, logloss = cross_val_loro(dataset, list(range(_N_PREDICTORS)), l2=1.0)

    scored = {repo for repo, _, _ in per_repo}
    assert "r0" not in scored
    assert {"r1", "r2"} <= scored
    assert logloss == logloss  # not NaN: other repos still contribute


def test_payload_shape_and_rounding() -> None:
    dataset = _dataset(signal_col=0, seed=5)
    payload = to_payload(run_ablation(dataset, bootstrap_draws=20))

    assert payload["schema"] == 1
    snapshot = payload["snapshot"]
    assert isinstance(snapshot, dict)
    assert snapshot["n_repos"] == 4
    model = payload["model"]
    assert isinstance(model, dict)
    assert model["predictors_full"] == list(CONTINUOUS_PREDICTORS)
    assert len(model["predictors_drop"]) == _N_PREDICTORS - 1
    assert "verdict" in payload


def test_committed_ablation_json_is_valid_if_present() -> None:
    """If the committed snapshot exists, it must parse and reference only enabled repos."""
    path = CALIBRATION_DIR / "ablation.json"
    if not path.exists():
        pytest.skip("ablation.json not generated yet (human-run step)")
    data = json.loads(path.read_text(encoding="utf-8"))
    enabled = {p.parent.name for p in REPOS_DIR.glob("*/defect-labels.json")}
    repos = data["snapshot"]["repos"]
    assert set(repos) <= enabled
    assert data["snapshot"]["n_buggy"] > 0
    assert data["file_line_coefficient"]["sign"] in {"positive", "negative"}
