# Calibration data (P21)

This directory holds the committed inputs and rollups for the empirical
calibration harness (`bin/calibration/`). The cloned repos and per-revision
coverage live under `corpus/` and `_cache/` and are **gitignored**; only the
rollups and the hand-authored config are committed.

The committed rollups are a deliberately-frozen **snapshot**, not reproducible
from a script alone. Two inputs are not fixed by the harness: `gh pr list`
returns whatever is merged upstream *now* (so the replayed PR set drifts unless
the SHAs are pinned in `pr-labels.toml`), and each repo's own test suite can be
flaky or time-dependent (so regenerated coverage — and therefore scores — can
shift between runs). Re-running with the same pinned SHAs and a deterministic
suite reproduces the digests; nothing weaker does.

## Files

| File | Committed | What it is |
| --- | --- | --- |
| `corpus.toml` | yes | the repos to replay + their coverage recipe |
| `pr-labels.toml` | yes | manual accepted/rejected outcome labels, pinned to SHAs |
| `pr-replay-rollup.json` | yes | per-PR regression digests from a replay run |
| `sprawl-candidates.json` | yes | accept/reject separation per sprawl candidate |
| `defect-labels.json` | yes | SZZ defect-implication labels per function at snapshot S (phase 2) |
| `defect-prediction.json` | yes | per-candidate AUC of score vs the SZZ defect label (phase 2) |
| `ignore-revs/<repo>.txt` | yes | optional mass-reformat SHAs to skip during blame |
| `sprawl-experiment.json` | yes | the P24 inter-metric investigation (0.2.9) |
| `corpus/`, `_cache/` | no | clones, venvs, per-SHA coverage + analyze/blame cache |

`pr-replay-rollup.json` and `sprawl-candidates.json` are checked in **empty**: no
PRs have been replayed or labelled yet. Populating them is a deliberate, human-run
step (it clones real repos and runs their suites under coverage — minutes to hours,
needs `gh` auth and network), not something CI does.

## Populating (phase 1)

```bash
# 1. Replay recent merged PRs for the enabled repos (requests, httpx, rich).
#    Per-SHA results are cached under _cache/, so re-runs are cheap.
uv run python -m bin.calibration.harness replay --repos requests --max-prs 10

# 2. Hand-label the replayed PRs in pr-labels.toml (accepted / rejected),
#    pinning the exact base_sha / head_sha the replay used (see the rollup).

# 3. Re-run replay so the rollup picks up the labels, then evaluate candidates.
uv run python -m bin.calibration.harness replay --repos requests --max-prs 10
uv run python -m bin.calibration.harness rescore
```

`rescore` re-scores each labelled PR under the swept candidate sprawl fixes
(drop the file-line term, shrink its file share at 0.60/0.75/0.90, raise the
file-line band at 750/1500, 1000/2000, 1500/3000) plus the shipped baseline, and
reports whether each candidate makes *rejected* PRs carry more regressions than
*accepted* ones. With only a handful of labels the separation is directional,
not significant — the rollup says so.

## On the label — and why phase 1's is weak

The accept/reject label here is a **deliberately weak phase-1 proxy**, and it is
the harness's biggest limitation. Two problems:

1. **It is hand-labelled, so it won't scale.** You will realistically tag tens of
   PRs, not thousands. A separation statistic on n that small is noise.
2. **The "rejected" class is nearly empty by construction.** Almost every merged
   PR was *accepted* — that is why it is in the history. Changes rejected
   *specifically for maintainability* (a sprawl-driven revert, a "please don't
   split this" review) are rare and hard to find in merge history, so the bucket
   the whole separation analysis depends on starves.

The Abreu et al. (2024) work this is modelled on did **not** use reviewer
acceptance; it used **real labelled outcomes** (severe incidents). The obtainable,
literature-standard equivalent for an OSS corpus is **defect-linking (SZZ)**:
mine bug-fixing commits, `git blame` the fixed lines to the commits that last
touched them, and map those to functions. The label becomes "was this function
later implicated in a bug-fix" — derived from git history, **no manual labelling**,
and far closer to the real risk question than "did a reviewer accept the PR."

Phase 1 ships the *plumbing* (replay → coverage → diff → re-score → separation)
and validates it end-to-end, but the accept/reject label is a placeholder.

**Phase 2 (shipped) replaces it with the SZZ defect-linker.** The `defects`
subcommand scores every function at a historical snapshot `S`, mines bug-fix
commits in `(S, HEAD]`, `git blame`s their deleted lines to the introducing
function, and tracks that function back to `S` (exact id, else fingerprint match).
The `predict` subcommand then reports, per sprawl candidate, the **AUC** of the
score against the defect label:

```bash
uv run python -m bin.calibration.harness defects --repos requests --snapshot-days 365 --max-fixes 50
uv run python -m bin.calibration.harness predict
# inspect defect-labels.json + defect-prediction.json
```

Read `defect-prediction.json` as: `total_auc(drop_file_line) > total_auc(baseline)`
means the file-line sprawl term is **noise** (removing it improves defect
prediction); lower means it is **signal**; `sprawl_auc ≈ 0.5` means the component
is non-predictive regardless of blend. Caveats that bound any conclusion: SZZ
fix-detection is heuristic; functions added after `S` or refactored past the rename
threshold are censored/untracked (counted in the label file); a single repo's AUC
is directional — pool the corpus first.

`defect-labels.json` / `defect-prediction.json` are checked in **empty**;
populating them is the same human-run, networked step as the phase-1 rollups.

**No product weight change ships from this.** Re-scoring is analysis only; any
weight change waits on a real outcome label and enough data to be defensible (see
`docs/sprawl-component-finding.md` and the 0.2.x roadmap).
