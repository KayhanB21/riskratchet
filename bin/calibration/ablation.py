"""Pooled, repo-stratified logistic-regression ablation of the sprawl file-line term.

This is the decision-gate model that `data/calibration/defect-prediction-findings.md`
§6.6/§7 promised but deferred: the descriptive phase-2 study compared per-repo AUC of
the score against an SZZ defect label, but per-repo AUCs disagree on sign, so a naive
pooled AUC is dominated by the biggest (anti-predictive) repos. Here we fit a proper
pooled model instead.

Design:

* **Predictors.** The six risk components, but with `sprawl` split into its two halves —
  the function-length term and the file-line term (`rescore._function_term` /
  `_file_term`) — so the file-line term gets its own coefficient *controlling for* the
  function-length half and the other five components.
* **Stratification.** One regularized intercept per repo (a one-hot block), which absorbs
  each repo's base defect rate (the F5 heterogeneity).
* **Validation.** Leave-one-repo-out (LORO): fit on the other repos, score the held-out
  repo with the shared continuous slopes only, and take the *within-repo* AUC. That AUC
  is invariant to the held-out repo's unknown intercept (a constant shift does not change
  ranking), which is exactly why LORO is valid here. We do this for the **full** predictor
  set and for the set with the **file-line term dropped**, and compare.
* **Inference.** A single pooled model reports the standardized file-line coefficient with
  a repo-clustered bootstrap 95% CI (resample repos with replacement, refit).

Engine: L2-regularized logistic regression via `scipy.optimize.minimize` (L-BFGS-B);
numpy for the matrices. Both are dev/calibration-only dependencies. **Analysis only — no
scoring or weight change ships from this; a weight change stays 0.3.0-gated.**
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize
from scipy.stats import binomtest

from bin.calibration.predict import auc_from_mwu
from bin.calibration.rescore import _file_term, _function_term
from riskratchet.models import FunctionId, FunctionRisk, RiskReport

# The seven continuous predictors. Order matters: FILE_LINE_INDEX points at the term
# the whole ablation is about. The first five are the already-normalized component
# scores; the last two are the two halves of the sprawl component, used raw.
CONTINUOUS_PREDICTORS: tuple[str, ...] = (
    "coverage_gap",
    "structural_complexity",
    "branch_gap",
    "churn",
    "public_surface",
    "sprawl_function_term",
    "sprawl_file_term",
)
FILE_LINE_INDEX = 6
DEFAULT_L2 = 1.0
BOOTSTRAP_DRAWS = 1000
BOOTSTRAP_SEED = 0
# "Non-trivial" CV-AUC change bar used only to phrase the verdict, not to gate anything.
TRIVIAL_AUC_DELTA = 0.005


@dataclass(frozen=True)
class Dataset:
    """The pooled feature matrix. `repo_index[i]` indexes into `repos`."""

    repos: tuple[str, ...]
    repo_index: np.ndarray  # (n,) int
    x: np.ndarray  # (n, 7) float
    y: np.ndarray  # (n,) float 0/1

    @property
    def n_functions(self) -> int:
        return int(self.x.shape[0])

    @property
    def n_buggy(self) -> int:
        return int(self.y.sum())


@dataclass(frozen=True)
class AblationResult:
    dataset: Dataset
    l2: float
    bootstrap_draws: int
    full_per_repo: list[tuple[str, int, float]]  # (repo, n_buggy, within-repo AUC)
    full_logloss: float
    drop_per_repo: list[tuple[str, int, float]]
    drop_logloss: float
    file_line_coef: float
    file_line_ci: tuple[float, float]


def _features(fn: FunctionRisk) -> list[float]:
    c = fn.components
    return [
        c.coverage_gap,
        c.structural_complexity,
        c.branch_gap,
        c.churn,
        c.public_surface,
        _function_term(fn),
        _file_term(fn),
    ]


def build_dataset(per_repo: list[tuple[str, RiskReport, set[FunctionId]]]) -> Dataset:
    """Pool `(repo_name, scored_report, defect_function_ids)` into one matrix.

    Deterministic row order (repo, then `path::qualname`) so the fit and every committed
    number are reproducible.
    """
    repos = tuple(sorted(name for name, _, _ in per_repo))
    idx_of = {name: i for i, name in enumerate(repos)}
    rows_x: list[list[float]] = []
    rows_y: list[float] = []
    rows_r: list[int] = []
    for name, report, defect_ids in sorted(per_repo, key=lambda t: t[0]):
        for fn in sorted(report.functions, key=lambda f: f.id.as_target()):
            rows_x.append(_features(fn))
            rows_y.append(1.0 if fn.id in defect_ids else 0.0)
            rows_r.append(idx_of[name])
    return Dataset(
        repos=repos,
        repo_index=np.asarray(rows_r, dtype=int),
        x=np.asarray(rows_x, dtype=float).reshape(-1, len(CONTINUOUS_PREDICTORS)),
        y=np.asarray(rows_y, dtype=float),
    )


def _standardize(
    x: np.ndarray, mean: np.ndarray | None = None, std: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if mean is None:
        mean = x.mean(axis=0)
    if std is None:
        std = x.std(axis=0)
        std = np.where(std == 0.0, 1.0, std)
    return (x - mean) / std, mean, std


def _one_hot(repo_index: np.ndarray, n_repos: int) -> np.ndarray:
    m = np.zeros((repo_index.shape[0], n_repos))
    m[np.arange(repo_index.shape[0]), repo_index] = 1.0
    return m


def _objective(beta: np.ndarray, x: np.ndarray, y: np.ndarray, l2: float) -> tuple[float, np.ndarray]:
    """Penalized negative log-likelihood + analytic gradient (numerically stable)."""
    z = x @ beta
    nll = float(np.sum(np.logaddexp(0.0, z) - y * z) + 0.5 * l2 * float(np.dot(beta, beta)))
    p = 1.0 / (1.0 + np.exp(-z))
    grad = x.T @ (p - y) + l2 * beta
    return nll, grad


def _fit(x: np.ndarray, y: np.ndarray, l2: float) -> np.ndarray:
    beta0 = np.zeros(x.shape[1])
    res = minimize(_objective, beta0, args=(x, y, l2), jac=True, method="L-BFGS-B")
    return np.asarray(res.x, dtype=float)


def _fit_with_repo_effects(
    x_cont: np.ndarray, repo_index: np.ndarray, y: np.ndarray, l2: float
) -> np.ndarray:
    """Fit on standardized continuous predictors + a per-repo one-hot intercept block.

    Returns the full coefficient vector; the first `x_cont.shape[1]` entries are the
    shared continuous slopes (the only part used to score an unseen repo).
    """
    x_s, _, _ = _standardize(x_cont)
    uniq = sorted(set(repo_index.tolist()))
    remap = {r: i for i, r in enumerate(uniq)}
    compact = np.asarray([remap[r] for r in repo_index.tolist()], dtype=int)
    design = np.hstack([x_s, _one_hot(compact, len(uniq))])
    return _fit(design, y, l2)


def cross_val_loro(
    dataset: Dataset, cols: list[int], l2: float
) -> tuple[list[tuple[str, int, float]], float]:
    """Leave-one-repo-out CV. Returns per-repo within-repo AUC + pooled OOF log-loss."""
    per_repo: list[tuple[str, int, float]] = []
    losses: list[float] = []
    for held in range(len(dataset.repos)):
        test_mask = dataset.repo_index == held
        train_mask = ~test_mask
        if not test_mask.any() or not train_mask.any():
            continue
        x_tr = dataset.x[train_mask][:, cols]
        y_tr = dataset.y[train_mask]
        beta = _fit_with_repo_effects(x_tr, dataset.repo_index[train_mask], y_tr, l2)
        beta_cont = beta[: len(cols)]
        _, mean, std = _standardize(x_tr)
        x_te, _, _ = _standardize(dataset.x[test_mask][:, cols], mean, std)
        y_te = dataset.y[test_mask]
        linpred = x_te @ beta_cont
        auc = auc_from_mwu(linpred[y_te == 1.0].tolist(), linpred[y_te == 0.0].tolist())
        if auc == auc:  # not NaN: held-out repo had both classes
            per_repo.append((dataset.repos[held], int(y_te.sum()), auc))
        # Secondary, intercept-dependent: OOF log-loss using the train base rate.
        base = float(np.clip(y_tr.mean(), 1e-6, 1.0 - 1e-6))
        prob = 1.0 / (1.0 + np.exp(-(linpred + np.log(base / (1.0 - base)))))
        prob = np.clip(prob, 1e-12, 1.0 - 1e-12)
        losses.extend((-(y_te * np.log(prob) + (1.0 - y_te) * np.log(1.0 - prob))).tolist())
    pooled_logloss = float(np.mean(losses)) if losses else float("nan")
    return per_repo, pooled_logloss


def file_line_coefficient(dataset: Dataset, l2: float) -> float:
    """Standardized file-line coefficient from one pooled model over all repos."""
    beta = _fit_with_repo_effects(dataset.x, dataset.repo_index, dataset.y, l2)
    return float(beta[FILE_LINE_INDEX])


def file_line_ci(
    dataset: Dataset, l2: float, *, draws: int = BOOTSTRAP_DRAWS, seed: int = BOOTSTRAP_SEED
) -> tuple[float, float]:
    """Repo-clustered bootstrap 95% CI for the file-line coefficient (seeded → stable)."""
    rng = np.random.default_rng(seed)
    n = len(dataset.repos)
    rows_by_repo = [np.where(dataset.repo_index == r)[0] for r in range(n)]
    coefs: list[float] = []
    for _ in range(draws):
        sampled = rng.integers(0, n, size=n)
        x_parts: list[np.ndarray] = []
        y_parts: list[np.ndarray] = []
        r_parts: list[np.ndarray] = []
        for new_id, r in enumerate(sampled.tolist()):
            idx = rows_by_repo[r]
            x_parts.append(dataset.x[idx])
            y_parts.append(dataset.y[idx])
            r_parts.append(np.full(idx.shape[0], new_id, dtype=int))
        beta = _fit_with_repo_effects(
            np.vstack(x_parts), np.concatenate(r_parts), np.concatenate(y_parts), l2
        )
        coefs.append(float(beta[FILE_LINE_INDEX]))
    lo, hi = np.percentile(coefs, [2.5, 97.5])
    return float(lo), float(hi)


def run_ablation(
    dataset: Dataset, *, l2: float = DEFAULT_L2, bootstrap_draws: int = BOOTSTRAP_DRAWS
) -> AblationResult:
    full_cols = list(range(len(CONTINUOUS_PREDICTORS)))
    drop_cols = [i for i in full_cols if i != FILE_LINE_INDEX]
    full_per_repo, full_logloss = cross_val_loro(dataset, full_cols, l2)
    drop_per_repo, drop_logloss = cross_val_loro(dataset, drop_cols, l2)
    coef = file_line_coefficient(dataset, l2)
    ci = file_line_ci(dataset, l2, draws=bootstrap_draws)
    return AblationResult(
        dataset=dataset,
        l2=l2,
        bootstrap_draws=bootstrap_draws,
        full_per_repo=full_per_repo,
        full_logloss=full_logloss,
        drop_per_repo=drop_per_repo,
        drop_logloss=drop_logloss,
        file_line_coef=coef,
        file_line_ci=ci,
    )


def _auc_mean(per_repo: list[tuple[str, int, float]]) -> float:
    aucs = [a for _, _, a in per_repo]
    return float(np.mean(aucs)) if aucs else float("nan")


def _auc_weighted(per_repo: list[tuple[str, int, float]]) -> float:
    if not per_repo:
        return float("nan")
    weights = float(sum(n for _, n, _ in per_repo))
    if weights == 0.0:
        return float("nan")
    return float(sum(n * a for _, n, a in per_repo) / weights)


def _paired_sign_test(result: AblationResult) -> tuple[int, int, float]:
    """Across repos scored in both CV runs: (full-better count, comparable count, p)."""
    full = {repo: auc for repo, _, auc in result.full_per_repo}
    drop = {repo: auc for repo, _, auc in result.drop_per_repo}
    deltas = [full[r] - drop[r] for r in full.keys() & drop.keys()]
    nonzero = [d for d in deltas if d != 0.0]
    full_better = sum(1 for d in nonzero if d > 0.0)
    if not nonzero:
        return full_better, 0, float("nan")
    p = float(binomtest(full_better, len(nonzero), 0.5).pvalue)
    return full_better, len(nonzero), p


def _round(value: float) -> float | None:
    return None if value != value else round(value, 4)  # value != value: NaN


def verdict(result: AblationResult) -> str:
    """Plain-language read of the model, templated from the numbers (never hardcoded)."""
    delta = _auc_mean(result.full_per_repo) - _auc_mean(result.drop_per_repo)
    lo, hi = result.file_line_ci
    ci_excludes_zero = (lo > 0.0) or (hi < 0.0)
    if delta <= TRIVIAL_AUC_DELTA and not ci_excludes_zero:
        signal = (
            "The file-line sprawl term carries no defensible independent signal: dropping it "
            f"does not reduce pooled leave-one-repo-out AUC (Δ={delta:+.3f}) and its coefficient's "
            f"95% CI [{lo:.3f}, {hi:.3f}] spans zero. Consistent with the 0.3.0 candidate to drop or "
            "shrink it."
        )
    elif delta > TRIVIAL_AUC_DELTA and result.file_line_coef > 0.0 and ci_excludes_zero:
        signal = (
            "The file-line sprawl term carries independent positive signal: keeping it raises "
            f"pooled leave-one-repo-out AUC (Δ={delta:+.3f}) and its coefficient's 95% CI "
            f"[{lo:.3f}, {hi:.3f}] excludes zero."
        )
    else:
        signal = (
            f"Mixed: pooled AUC change from dropping the file-line term is Δ={delta:+.3f} and its "
            f"coefficient's 95% CI is [{lo:.3f}, {hi:.3f}] — not a clean signal-or-noise verdict."
        )
    return (
        signal + " Analysis only: no scoring or weight change ships here; any weight change stays a "
        "0.3.0 breaking change, and the construct gap (defects ≠ maintainability) and "
        "external-validity gap (mature OSS ≠ the target population) remain open."
    )


_NOTE = (
    "Pooled, repo-stratified L2 logistic regression (one regularized intercept per repo "
    "absorbs the heterogeneity), validated leave-one-repo-out. Compares the full six-component "
    "score (sprawl split into its function-length and file-line halves) against dropping the "
    "file-line half; reports the file-line coefficient with a repo-clustered bootstrap CI. This "
    "is the decision-gate model promised in defect-prediction-findings.md §6.6/§7. Within-repo "
    "AUC is intercept-invariant, which is why LORO is valid; log-loss is a secondary, "
    "intercept-dependent check. Analysis only; no weight change."
)


def to_payload(result: AblationResult) -> dict[str, object]:
    """Churn-resistant JSON payload: sorted rows, floats rounded, NaN → null."""
    full_better, comparable, sign_p = _paired_sign_test(result)
    lo, hi = result.file_line_ci

    def repo_rows(per_repo: list[tuple[str, int, float]]) -> list[dict[str, object]]:
        return [
            {"repo": repo, "n_buggy": n, "auc": _round(auc)}
            for repo, n, auc in sorted(per_repo, key=lambda t: t[0])
        ]

    return {
        "schema": 1,
        "note": _NOTE,
        "snapshot": {
            "n_repos": len(result.dataset.repos),
            "n_functions": result.dataset.n_functions,
            "n_buggy": result.dataset.n_buggy,
            "repos": list(result.dataset.repos),
        },
        "model": {
            "engine": "scipy L-BFGS-B, L2 logistic regression",
            "l2": result.l2,
            "standardized": True,
            "fixed_effects": "repo",
            "cv": "leave-one-repo-out",
            "predictors_full": list(CONTINUOUS_PREDICTORS),
            "predictors_drop": [p for i, p in enumerate(CONTINUOUS_PREDICTORS) if i != FILE_LINE_INDEX],
        },
        "results": {
            "full": {
                "auc_mean": _round(_auc_mean(result.full_per_repo)),
                "auc_weighted": _round(_auc_weighted(result.full_per_repo)),
                "logloss": _round(result.full_logloss),
                "per_repo_auc": repo_rows(result.full_per_repo),
            },
            "drop_file_line": {
                "auc_mean": _round(_auc_mean(result.drop_per_repo)),
                "auc_weighted": _round(_auc_weighted(result.drop_per_repo)),
                "logloss": _round(result.drop_logloss),
                "per_repo_auc": repo_rows(result.drop_per_repo),
            },
        },
        "ablation": {
            "delta_auc_full_minus_drop_mean": _round(
                _auc_mean(result.full_per_repo) - _auc_mean(result.drop_per_repo)
            ),
            "delta_auc_weighted": _round(
                _auc_weighted(result.full_per_repo) - _auc_weighted(result.drop_per_repo)
            ),
            "n_repos_full_better": f"{full_better}/{comparable}",
            "sign_test_p": _round(sign_p),
        },
        "file_line_coefficient": {
            "standardized": _round(result.file_line_coef),
            "sign": "positive" if result.file_line_coef > 0 else "negative",
            "ci95": [_round(lo), _round(hi)],
            "method": f"repo-clustered bootstrap, seed {BOOTSTRAP_SEED}, {result.bootstrap_draws} draws",
        },
        "verdict": verdict(result),
    }
