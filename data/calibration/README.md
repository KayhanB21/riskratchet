# Calibration data (P21)

This directory holds the committed inputs and rollups for the empirical
calibration harness (`bin/calibration/`). The cloned repos and per-revision
coverage live under `corpus/` and `_cache/` and are **gitignored**; only the
reproducible rollups and the hand-authored config are committed.

## Files

| File | Committed | What it is |
| --- | --- | --- |
| `corpus.toml` | yes | the repos to replay + their coverage recipe |
| `pr-labels.toml` | yes | manual accepted/rejected outcome labels, pinned to SHAs |
| `pr-replay-rollup.json` | yes | per-PR regression digests from a replay run |
| `sprawl-candidates.json` | yes | accept/reject separation per sprawl candidate |
| `sprawl-experiment.json` | yes | the P24 inter-metric investigation (0.2.9) |
| `corpus/`, `_cache/` | no | clones, venvs, per-SHA coverage + analyze cache |

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

`rescore` re-scores each labelled PR under the three candidate sprawl fixes
(drop the file-line term, shrink its share, raise the 500/1000 band) plus the
shipped baseline, and reports whether each candidate makes *rejected* PRs carry
more regressions than *accepted* ones. With only a handful of labels the
separation is directional, not significant — the rollup says so.

**No product weight change ships from this.** Re-scoring is analysis only; any
weight change waits on enough labelled outcome data to be defensible (see
`docs/sprawl-component-finding.md` and the 0.2.x roadmap).
