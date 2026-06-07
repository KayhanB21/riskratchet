# Maintainability via change-proneness — method and preliminary findings (P21, phase 4)

Status: **full 34-repo polished corpus run and twice-verified (byte-identical across two
runs — deterministic); the messy gradient cohort and the review-comment anchor remain
unrun.** This phase attacks the two gaps the phase-2/3 defect study named and could not
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

## 2. Result (34-repo polished corpus: 33,490 functions, 4,515 change-prone)

The full polished corpus, scored coverage-free at S and labelled from git history. Each
repo was run **twice** and produced byte-identical output (deterministic), so the numbers
below are reproducible, not a single noisy draw.

### 2.1 Headline: structure beats the activity null — significantly

| model | pooled LORO AUC (mean) | weighted |
| --- | --- | --- |
| null (past-churn only) | 0.574 | 0.582 |
| full (+ structural signals) | **0.661** | 0.652 |

- **Structural signals beat the activity null by Δ=+0.086** (mean; +0.071 weighted), full
  better in **30/34 repos**, sign-test **p ≈ 0**. Unlike the defect study — where the
  shipped score was anti-predictive on the big repos — the structural signals here add real,
  statistically solid value *over* "active code stays active." (The n=4 validation gave the
  same Δ≈+0.083 but could not reach significance; the full corpus confirms it.)
- **The file-line sprawl half stays net-noise** under this maintainability outcome too:
  standardized coef **+0.039, 95% CI [−0.046, +0.162]** — **spans zero**, consistent with
  phase 3 (the sign is unstable run-to-run; the magnitude is indistinguishable from zero).

### 2.2 Per-signal ablation: the lift is almost entirely *complexity*

Decomposing the +0.086. Two views, both at the top-quartile label:

**(a) each structural signal added alone, on top of past-churn:**

| signal | AUC | Δ vs null |
| --- | --- | --- |
| structural_complexity | 0.654 | **+0.079** |
| sprawl_function_term | 0.602 | +0.027 |
| sprawl_file_term | 0.582 | +0.008 |
| public_surface | 0.561 | **−0.013** |

**(b) each structural signal dropped from the full model (cost of its absence):**

| drop signal | AUC | Δ vs full |
| --- | --- | --- |
| structural_complexity | 0.598 | **−0.063** |
| sprawl_function_term | 0.658 | −0.003 |
| sprawl_file_term | 0.660 | −0.001 |
| public_surface | 0.660 | −0.001 |

**Read:** `structural_complexity` alone takes the AUC from 0.574 to 0.654 — almost the entire
full-model lift of 0.661. Dropping complexity collapses the model (−0.063); dropping *any*
of the other three costs ≤0.003. The function-length sprawl half has a small *standalone*
signal (+0.027) but is **redundant with complexity** (adds nothing once complexity is in).
`public_surface` is net-negative alone. So the honest claim tightens: **complexity beats
activity; the remaining components are redundant or inert under this outcome.** This widens
the phase-3 "does the file-line term earn its weight?" question to most of the non-coverage
components — a flag for the 0.3.0 weight review, not a weight change here.

### 2.3 Binarization sensitivity: the headline is robust to the threshold

"Change-prone = top quartile of future edits" is a modelling choice. Re-running null-vs-full
at three thresholds shows the conclusion does not hinge on it:

| threshold | % positive | null | full | Δ (full−null) | full better |
| --- | --- | --- | --- | --- | --- |
| top-decile (0.10) | 8.1% | 0.585 | 0.682 | **+0.097** | 31/34 |
| top-quartile (0.25) | 13.5% | 0.574 | 0.661 | **+0.086** | 30/34 |
| median (0.50) | 16.5% | 0.566 | 0.651 | **+0.084** | 28/34 |

Δ stays in a tight +0.084 to +0.097 band and full beats null in 28–31 of 34 repos at every
cutoff (slightly *stronger* at the strict end). The binarization choice is not load-bearing.

### 2.4 What is still not done

This is the **polished end of the gradient only**. The `messy` tier (smaller, younger,
untested `coverage_free` repos) is empty, so the external-validity question — does this hold
as code gets messier, toward the AI-side-project target? — has **no data yet**. The
review-comment construct anchor (§1.5) is built but **unrun**. Both remain human-run steps.

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
