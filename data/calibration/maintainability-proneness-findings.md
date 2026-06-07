# Maintainability via change-proneness — method and preliminary findings (P21, phase 4)

Status: **machinery shipped + validated on a 4-repo sample; full corpus + gradient run is
human-run.** This phase attacks the two gaps the phase-2/3 defect study named and could not
close (`defect-prediction-findings.md` §6.1 construct, §6.2 external validity):

1. **Construct.** Phase 2/3 measured *defects* (SZZ bug-fixes); the tool claims
   *maintainability*. Here the outcome is **change-proneness** — how often/heavily a function
   is edited in the future — a standard maintainability proxy (closer to "hard to change"
   than "had a bug").
2. **External validity.** The defect corpus needs a runnable test suite for coverage, which
   excludes the messy, untested code riskratchet targets. Here scoring is **coverage-free**
   (source + git only, no test run), so untested repos are in scope, and we sweep a
   **polished → messy gradient** asking whether conclusions hold as repos get messier.

**Analysis only. No scoring or weight change ships from this document.**

## 1. Method

Per repo, at a historical snapshot `S` (~365 days before HEAD):

1. **Score coverage-free** (`bin/calibration/coverage_free.py`): check out `S`, run the
   engine with `coverage_path=None`. The four static signals — `structural_complexity`,
   `sprawl` (split into its function-length and file-line halves), `public_surface`,
   `churn` — are real; the two coverage components are constant and dropped. No venv, no
   suite — so any repo with git history qualifies.
2. **Label change-proneness** (`proneness.py` + `change_counting.py`): count commits in
   `(S, HEAD]` that touch each function (NEW-side diff ranges → functions parsed at that
   commit → overlap → tracked back to `S` by id/fingerprint). Binarize to **change-prone =
   the within-repo top quartile of future edit-count** (~25% positives — far more signal
   than the 2–3% defect rate).
3. **Past-churn null feature**: the *same* count over the past window `(S-window, S]`.
4. **The model** (`proneness_model.py`, reuses `ablation.cross_val_loro`): two pooled,
   repo-stratified L2 logistic regressions, leave-one-repo-out, repo fixed-effects.
   - **null**: `change_prone ~ past_churn + repo`
   - **full**: `change_prone ~ past_churn + complexity + sprawl_fn + sprawl_file + public + repo`

   The confound this guards against: future edits are trivially predicted by past edits
   (activity autocorrelation). So **the structural signals must beat the past-churn null** —
   ΔAUC = full − null, with a repo-clustered bootstrap. We also re-ask the phase-3 question
   under this outcome (is the file-line sprawl half still noise?) and bucket per-repo Δ by
   gradient tier.
5. **Construct anchor** (`review_comments.py`): mine "split this / too complex / refactor"
   PR review comments, map each to its function, and check whether human-flagged functions
   are in fact more change-prone (`flag_agreement_auc`). A directional human-judgment check
   on the proxy — not a primary label.

## 2. Preliminary result (4-repo validation: loguru, sqlparse, tenacity, wrapt)

A small sanity run (790 functions, 161 change-prone) — **directional only, n=4, not the
committed dataset**:

| model | pooled LORO AUC (mean) |
| --- | --- |
| null (past-churn only) | 0.575 |
| full (+ structural signals) | **0.658** |

- **Structural signals beat the activity null by Δ≈+0.083** (full better in 3/4 repos).
  Unlike the defect study — where the shipped score was anti-predictive on the big repos —
  the structural signals here add real value *over* "active code stays active." Suggestive,
  not significant (sign-test p≈0.63 at n=4).
- **The file-line sprawl half stays net-noise** under this maintainability outcome too:
  standardized coef ≈ −0.63, 95% CI [−1.00, +0.20] **spans zero** — consistent with phase 3.

This is the experiment working end-to-end on real data; it is **not** a conclusion. The
full corpus (the 34 polished repos re-scored coverage-free + a gradient cohort of messier,
untested repos) is the human-run step.

## 3. Running it (human-run)

```bash
# coverage-free change-proneness labels (clones; no test suite, fast)
uv run python -m bin.calibration.harness proneness --repos <name|all>
# optional human-judgment anchor (needs gh)
uv run python -m bin.calibration.harness review-flags --repos <name>
# the null-vs-full model (needs the calibration group)
uv run --group calibration python -m bin.calibration.harness proneness-model
# inspect data/calibration/proneness-ablation.json
```

The **gradient cohort** is added by appending `coverage_free = true` repos to the corpus
(`repos/<name>/repo.toml`, no `test_command` needed). Selection criteria for the messy end:
smaller, younger (but ≥~2yr history — a window each side of `S`), lower test-coverage,
faster churn, solo/small-team tools. They anchor the messy end; the existing 34 anchor the
polished end. The headline question is **stability of the conclusions along that gradient**.

## 4. Threats / limitations

- **Change-proneness is a proxy** — "got edited," not "was painful to edit." The past-churn
  null is the guard (structure must beat activity); the review-comment anchor is the
  construct check. Neither makes it *maintainability* — it is *closer*, honestly labelled.
- **Coverage-free drops the strongest signal.** Phase 3 found coverage-derived components
  carried much of the defect signal; removing them is the price of reaching untested repos.
  The two studies measure different feature sets on purpose.
- **Gradient, not arrival.** "Messier OSS" is not AI-side-project code; the claim is
  stability *toward* the target, not *at* it.
- **≥2yr history** excludes the youngest side-projects (recorded per repo as
  `insufficient_past_history`).
- **Review comments** are sparse, review-culture-biased (so they live on the polished end),
  and line-shift on rebase/squash — directional, never primary.
- **Quartile binarization** is a modelling choice; report sensitivity (median vs top
  quartile) if a conclusion hinges on it.

## 5. References

- The gaps this phase probes: `defect-prediction-findings.md` §6.1 (construct), §6.2
  (external validity), §7 (decision rule — weights stay unchanged).
- Code: `bin/calibration/{coverage_free,change_counting,proneness,proneness_model,review_comments}.py`.
- Change-proneness as a maintainability proxy is standard in the code-smell / change-proneness
  literature; this harness applies it with a past-activity null control.
