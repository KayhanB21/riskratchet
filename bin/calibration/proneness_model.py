"""Does structure predict maintenance burden *beyond* prior activity?

The confound: a function edited a lot in the future is trivially predicted by being edited
a lot in the past (autocorrelation of activity, not maintainability). So past-churn is the
**null model** and the structural signals must beat it. We fit two pooled, repo-stratified
L2 logistic regressions on the change-prone label (leave-one-repo-out, repo fixed-effects,
reusing `ablation.cross_val_loro`):

* **null**: `change_prone ~ past_churn + repo`
* **full**: `change_prone ~ past_churn + complexity + sprawl_fn + sprawl_file + public + repo`

Structural signals are maintainability-predictive **iff** full beats null (ΔAUC, with a
repo-clustered bootstrap CI). We also re-ask the phase-3 question under this outcome: is the
file-line sprawl half still noise? And we bucket the per-repo Δ by a polished→messy gradient
tier. Features are coverage-free, so untested repos are in scope. **Analysis only.**
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import binomtest

from bin.calibration.ablation import (
    Dataset,
    _auc_mean,
    _auc_weighted,
    _fit_with_repo_effects,
    _round,
    cross_val_loro,
)
from bin.calibration.defects import SnapshotPopulation
from bin.calibration.proneness import PronenessLabels
from bin.calibration.rescore import _file_term, _function_term
from riskratchet.models import FunctionRisk

FEATURES: tuple[str, ...] = (
    "past_churn",
    "structural_complexity",
    "sprawl_function_term",
    "sprawl_file_term",
    "public_surface",
)
NULL_COLS = [0]  # past activity only
FULL_COLS = [0, 1, 2, 3, 4]
FILE_LINE_COL = 3
DEFAULT_L2 = 1.0
BOOTSTRAP_DRAWS = 1000
BOOTSTRAP_SEED = 0
TRIVIAL_AUC_DELTA = 0.005


@dataclass(frozen=True)
class PronenessResult:
    dataset: Dataset
    tiers: dict[str, str]  # repo -> "polished" | "messy"
    l2: float
    bootstrap_draws: int
    null_per_repo: list[tuple[str, int, float]]
    null_logloss: float
    full_per_repo: list[tuple[str, int, float]]
    full_logloss: float
    file_line_coef: float
    file_line_ci: tuple[float, float]


def _features(fn: FunctionRisk, past_churn: int) -> list[float]:
    c = fn.components
    return [
        float(past_churn),
        c.structural_complexity,
        _function_term(fn),
        _file_term(fn),
        c.public_surface,
    ]


def build_proneness_dataset(
    per_repo: list[tuple[str, SnapshotPopulation, PronenessLabels]],
) -> Dataset:
    repos = tuple(sorted(name for name, _, _ in per_repo))
    idx_of = {name: i for i, name in enumerate(repos)}
    rows_x: list[list[float]] = []
    rows_y: list[float] = []
    rows_r: list[int] = []
    for name, snapshot, labels in sorted(per_repo, key=lambda t: t[0]):
        for fn in sorted(snapshot.report.functions, key=lambda f: f.id.as_target()):
            past = labels.past.get(fn.id, (0, 0))[0]
            rows_x.append(_features(fn, past))
            rows_y.append(1.0 if fn.id in labels.change_prone else 0.0)
            rows_r.append(idx_of[name])
    return Dataset(
        repos=repos,
        repo_index=np.asarray(rows_r, dtype=int),
        x=np.asarray(rows_x, dtype=float).reshape(-1, len(FEATURES)),
        y=np.asarray(rows_y, dtype=float),
    )


def _coefficient_ci(dataset: Dataset, col: int, l2: float, *, draws: int, seed: int) -> tuple[float, float]:
    """Repo-clustered bootstrap 95% CI for the full-model coefficient at column `col`."""
    rng = np.random.default_rng(seed)
    n = len(dataset.repos)
    rows_by_repo = [np.where(dataset.repo_index == r)[0] for r in range(n)]
    coefs: list[float] = []
    for _ in range(draws):
        sampled = rng.integers(0, n, size=n)
        x_parts, y_parts, r_parts = [], [], []
        for new_id, r in enumerate(sampled.tolist()):
            idx = rows_by_repo[r]
            x_parts.append(dataset.x[idx])
            y_parts.append(dataset.y[idx])
            r_parts.append(np.full(idx.shape[0], new_id, dtype=int))
        beta = _fit_with_repo_effects(
            np.vstack(x_parts), np.concatenate(r_parts), np.concatenate(y_parts), l2
        )
        coefs.append(float(beta[col]))
    lo, hi = np.percentile(coefs, [2.5, 97.5])
    return float(lo), float(hi)


def run_proneness(
    dataset: Dataset,
    tiers: dict[str, str],
    *,
    l2: float = DEFAULT_L2,
    bootstrap_draws: int = BOOTSTRAP_DRAWS,
) -> PronenessResult:
    null_per_repo, null_ll = cross_val_loro(dataset, NULL_COLS, l2)
    full_per_repo, full_ll = cross_val_loro(dataset, FULL_COLS, l2)
    beta = _fit_with_repo_effects(dataset.x, dataset.repo_index, dataset.y, l2)
    coef = float(beta[FILE_LINE_COL])
    ci = _coefficient_ci(dataset, FILE_LINE_COL, l2, draws=bootstrap_draws, seed=BOOTSTRAP_SEED)
    return PronenessResult(
        dataset=dataset,
        tiers=tiers,
        l2=l2,
        bootstrap_draws=bootstrap_draws,
        null_per_repo=null_per_repo,
        null_logloss=null_ll,
        full_per_repo=full_per_repo,
        full_logloss=full_ll,
        file_line_coef=coef,
        file_line_ci=ci,
    )


def _paired_deltas(result: PronenessResult) -> dict[str, float]:
    """Per-repo delta AUC = full - null, for repos scored in both runs."""
    full = {repo: auc for repo, _, auc in result.full_per_repo}
    null = {repo: auc for repo, _, auc in result.null_per_repo}
    return {repo: full[repo] - null[repo] for repo in full.keys() & null.keys()}


def _gradient(result: PronenessResult) -> dict[str, object]:
    deltas = _paired_deltas(result)
    out: dict[str, object] = {}
    for tier in ("polished", "messy"):
        vals = [d for repo, d in deltas.items() if result.tiers.get(repo) == tier]
        out[tier] = {
            "n_repos": len(vals),
            "mean_delta_auc_full_minus_null": _round(float(np.mean(vals)) if vals else float("nan")),
        }
    return out


def verdict(result: PronenessResult) -> str:
    delta = _auc_mean(result.full_per_repo) - _auc_mean(result.null_per_repo)
    flo, fhi = result.file_line_ci
    file_line_noise = flo <= 0.0 <= fhi
    if delta > TRIVIAL_AUC_DELTA:
        head = (
            f"Structural signals beat the past-churn null: keeping complexity/sprawl/public raises "
            f"pooled leave-one-repo-out AUC by Δ={delta:+.3f} over activity alone. They carry "
            "maintenance-burden signal beyond 'active code stays active.'"
        )
    else:
        head = (
            f"Structural signals do NOT beat the past-churn null (Δ={delta:+.3f}); future edits are "
            "explained by past activity alone, so this corpus gives no evidence the structural "
            "signals predict maintenance burden."
        )
    tail = (
        f" The file-line sprawl half {'remains net-noise' if file_line_noise else 'carries signal'} "
        f"under this outcome (coef {result.file_line_coef:+.3f}, 95% CI [{flo:.3f}, {fhi:.3f}]). "
        "Analysis only; no weight change. Change-proneness is a proxy (got-edited, not "
        "was-painful), the corpus is a gradient toward — not at — the AI-side-project target, and "
        "the construct gap (edits != maintainability) is anchored, not closed, by the review-comment check."
    )
    return head + tail


_NOTE = (
    "Pooled, repo-stratified L2 logistic regression (repo fixed-effects), leave-one-repo-out. "
    "Null model uses past activity only; the full model adds the coverage-free structural signals. "
    "Delta AUC (full - null) with a repo-clustered bootstrap tests whether structure predicts "
    "future change-proneness beyond prior activity. Gradient buckets per-repo delta by tier. "
    "Analysis only; change-proneness is a maintainability proxy, not maintainability."
)


def to_payload(result: PronenessResult) -> dict[str, object]:
    deltas = _paired_deltas(result)
    nonzero = [d for d in deltas.values() if d != 0.0]
    full_better = sum(1 for d in nonzero if d > 0.0)
    sign_p = float(binomtest(full_better, len(nonzero), 0.5).pvalue) if nonzero else float("nan")
    flo, fhi = result.file_line_ci

    def repo_rows(per_repo: list[tuple[str, int, float]]) -> list[dict[str, object]]:
        return [
            {"repo": repo, "n_prone": n, "auc": _round(auc)}
            for repo, n, auc in sorted(per_repo, key=lambda t: t[0])
        ]

    return {
        "schema": 1,
        "note": _NOTE,
        "snapshot": {
            "n_repos": len(result.dataset.repos),
            "n_functions": result.dataset.n_functions,
            "n_change_prone": result.dataset.n_buggy,
            "repos": list(result.dataset.repos),
        },
        "model": {
            "engine": "scipy L-BFGS-B, L2 logistic regression",
            "l2": result.l2,
            "standardized": True,
            "fixed_effects": "repo",
            "cv": "leave-one-repo-out",
            "features_full": list(FEATURES),
            "features_null": [FEATURES[i] for i in NULL_COLS],
        },
        "results": {
            "null": {
                "auc_mean": _round(_auc_mean(result.null_per_repo)),
                "auc_weighted": _round(_auc_weighted(result.null_per_repo)),
                "logloss": _round(result.null_logloss),
                "per_repo_auc": repo_rows(result.null_per_repo),
            },
            "full": {
                "auc_mean": _round(_auc_mean(result.full_per_repo)),
                "auc_weighted": _round(_auc_weighted(result.full_per_repo)),
                "logloss": _round(result.full_logloss),
                "per_repo_auc": repo_rows(result.full_per_repo),
            },
        },
        "structure_beats_activity": {
            "delta_auc_full_minus_null_mean": _round(
                _auc_mean(result.full_per_repo) - _auc_mean(result.null_per_repo)
            ),
            "delta_auc_weighted": _round(
                _auc_weighted(result.full_per_repo) - _auc_weighted(result.null_per_repo)
            ),
            "n_repos_full_better": f"{full_better}/{len(nonzero)}",
            "sign_test_p": _round(sign_p),
        },
        "file_line_coefficient": {
            "standardized": _round(result.file_line_coef),
            "sign": "positive" if result.file_line_coef > 0 else "negative",
            "ci95": [_round(flo), _round(fhi)],
            "method": f"repo-clustered bootstrap, seed {BOOTSTRAP_SEED}, {result.bootstrap_draws} draws",
        },
        "gradient": _gradient(result),
        "verdict": verdict(result),
    }
