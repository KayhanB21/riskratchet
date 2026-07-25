# TypeScript complexity calibration (B2, 0.3.0)

**Result: `TYPESCRIPT_COMPLEXITY_CALIBRATION = (free=1, saturation=21)` — identical to Python's
`(1, 21)`, derived from data, not assumed.**

## Why a separate calibration at all

The two backends count cyclomatic complexity by deliberately different rules. TypeScript is
ESLint-faithful (`typescript_complexity.cyclomatic_for_node`); Python is radon/`ast`-faithful
(`complexity.py`). They diverge on two shapes — TS counts default parameters (Python does not), and
Python's manual fallback descends into nested functions (ESLint/TS prune them) — so the *raw* counts
are not directly comparable and the gap is **not a constant offset** (see
`docs/language-backend-contract.md §3`).

The 0.3.0 fix is **per-language normalization**: keep each raw count as displayed (`cx N` stays what a
TS dev's linter shows), but give the `structural_complexity` *normalization* its own `(free,
saturation)` band per backend, so the normalized 0–100 component represents the **same distribution
position** regardless of language. The contract requires the TS band be *derived*, never hand-picked.

## Method — endpoint-percentile match

`bin/calibration/ts_complexity_calibration.py`:

1. Measure the per-function cyclomatic distribution of a Python corpus and a TypeScript corpus.
2. Python normalizes with `(free=1, saturation=21)`. Find the percentiles those endpoints occupy in
   the Python distribution: `p_free = P(py_cc ≤ 1)`, `p_sat = P(py_cc ≤ 21)`.
3. Read the TS cyclomatic values at those **same percentiles**:
   `free_ts = quantile(ts_cc, p_free)`, `sat_ts = quantile(ts_cc, p_sat)`.

So a TS function at percentile *p* gets (at the anchors, and approximately between) the same
normalized score a Python function at percentile *p* gets — language-fair at the gate, ESLint-faithful
on screen.

## Corpus

- **TypeScript:** 12 mid-size, actively-developed, TS-heavy repos (`data/calibration/ts-complexity-corpus.toml`),
  libraries + apps, `.ts` and `.tsx`, tests/specs/stories/`.d.ts` excluded — **2,744 functions**.
  Repos: zod, zustand, hono, immer, ts-pattern, rxjs, class-validator, jotai, got, ink, table-core, swr.
  Exact cloned commit SHAs are recorded in `data/calibration/ts-complexity-calibration.json`.
- **Python:** the existing calibration corpus clones (polished + messy tiers) — **59,226 functions**.

## Numbers

| distribution | n | mean | p50 | p90 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|
| Python | 59,226 | 3.43 | 2 | 7 | 11 | 23 | 338 |
| TypeScript | 2,744 | 3.13 | 2 | 6 | 10 | 24.6 | 107 |

Anchors: `p_free = 0.4208` (42% of Python functions are straight-line, CC ≤ 1), `p_sat = 0.9885`.
At `p_sat`, TS cyclomatic ≈ **21.3** → rounds to **21**; at `p_free`, TS cyclomatic = **1**. So the
endpoint-matched TS band is `(1, 21)` — the same as Python.

The two distributions are strikingly close (identical p50, near-identical p90/p95). TS runs a hair
*lower* in the mid-range (p75: TS 3 vs PY 4; p90: TS 6 vs PY 7), so under the shared band a TS function
scores marginally lower than the Python function at the same percentile — a real, small distributional
difference, ≤5 normalized points, that a two-parameter band neither can nor should erase.

## Honest limitations

- **Thin saturation tail.** The `p_sat` anchor sits at the 98.85th percentile — ~1% of 2,744 ≈ 30
  functions above it — so the raw 21.3 is the least-stable digit. It rounds to Python's 21, and the
  conservative outcome (a *shared* band, introducing no unjustified divergence) is the safe one. If a
  larger TS corpus later shifts this materially, re-derive and bump the constant with a new rationale.
- **Displayed vs scored are decoupled by design.** `cx N` stays ESLint-faithful; only the normalized
  component uses this band. That is intentional, not a bug.
- **Cross-language coverage denominators remain non-comparable** (each backend scores its own
  fraction) — a separate §2 caveat, unaffected here.

## Reproduce

```bash
uv run --all-extras python -m bin.calibration.ts_complexity_calibration
# writes data/calibration/ts-complexity-calibration.json; prints the derived band
```

The constant lives in `src/riskratchet/scoring.py` (`TYPESCRIPT_COMPLEXITY_CALIBRATION`), a distinct
named value so a future re-derivation can move TS without touching Python. It is **unconsumed** until
the TS engine threads it into `compute_components` (B3); B2 ships the number and its provenance only.
