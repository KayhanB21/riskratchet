# P24: sprawl-component validation finding (0.2.9)

## Question

Every `0.2.x` module split (reporting in 0.2.6, baseline + config in 0.2.7)
scored "better" partly because moving functions into smaller files lowers their
`sprawl` component. The roadmap asks: does the per-file `sprawl` component
measure anything the per-function `structural_complexity` component does not —
i.e. **should** splitting a module into smaller files move scores at all?

This is the investigation. The experiment is reproducible:

```bash
uv run python bin/experiments/sprawl_vs_complexity.py
# raw data -> data/calibration/sprawl-experiment.json
```

## How sprawl is built

```python
sprawl = (function_line_score + file_line_score) / 2          # scoring.py:94
function_line_score = saturate(function.line_count, free=80,  saturation=160)
file_line_score     = saturate(file.total_lines,    free=500, saturation=1000)
```

So sprawl blends a **per-function** term (how long is this function) with a
**per-file** term (how long is the file it lives in). It contributes 10% of the
total score (`DEFAULT_WEIGHTS["sprawl"] = 0.10`).

The file-line term is the one under suspicion: it is **identical for every
function in a file**, and it is exactly what a module split changes.

## Evidence

### 1. Sprawl is not redundant with structural_complexity

Pearson correlation across riskratchet's own 275 functions / 29 files:

| pair | r |
| --- | ---: |
| `sprawl` vs `structural_complexity` | **0.28** |
| `file_line_term` vs `structural_complexity` | **0.17** |
| `function_line_term` vs `structural_complexity` | 0.45 |

The hypothesis "sprawl just re-measures complexity" is **rejected**. Sprawl is
largely orthogonal to `structural_complexity` (r=0.28), and the *file-line half*
is almost independent of it (r=0.17). The *function-line half* correlates
moderately (r=0.45) — longer functions tend to be more complex, as expected, but
not so strongly that it is double-counting.

Conclusion: sprawl measures **size**, not branching. It is a distinct signal.

### 2. The file-line term injects a file-level property into a per-function score

A controlled function — identical 40-line body, cyclomatic complexity 8, 50%
coverage — scored in three files of different size:

| file total lines | sprawl | structural_complexity | total score |
| ---: | ---: | ---: | ---: |
| 300 | 0.0 | 35.0 | 36.25 |
| 600 | 10.0 | 35.0 | 37.25 |
| 1200 | 50.0 | 35.0 | **41.25** |

The *same function* swings by **+5.0 points** purely on how big its file is.
`structural_complexity` does not move. Because the file-line term saturates
between 500 and 1000 lines, the full swing is `0.10 (weight) × 0.5 (half of
sprawl) × 100 = 5.0` points — enough to cross a severity band (the bands are
25 points wide) and enough to trip or satisfy the default component-regression
gate (tolerance 15 on a single component).

### 3. Splitting a file mechanically lowers every function in it

Simulating a cosmetic split of the largest file (`cli.py`, 1800 → 900 lines)
drops every function's score with **no change to any function body**. The
observed max drop here was only ~1.0 point — but only because `cli.py` at 1800
lines is already past the 1000-line saturation, so halving it stays in the
high band. A file crossing the 500–1000 band (the synthetic case above) moves
the full 5.0. The artifact is real; its magnitude depends on where the file
sits relative to the 500/1000 thresholds.

This is the 0.2.x module-split effect, confirmed: a split that leaves functions
byte-identical still improves their scores.

## Interpretation

Two defensible readings:

- **"File size is a real maintainability axis."** A function in a 1500-line
  god-module genuinely carries more cognitive load, more merge-conflict
  surface, and more navigation cost than the same function in a focused
  120-line module. Under this reading, a split that shrinks the file *is* a
  real (if modest) maintainability gain, and sprawl is correctly rewarding it.
- **"Per-function risk should depend on the function."** The file-line term is a
  file-level property smeared uniformly across every function in the file. Two
  functions with identical bodies, complexity, and coverage get different risk
  scores solely because of how many siblings they have. Under this reading, the
  file-line term is a category error in a *per-function* score.

Both readings are reasonable. The data does not adjudicate between them — that
requires knowing whether human reviewers *accept* split-driven score drops as
genuine improvements or dismiss them as noise, which is precisely the
accept/reject corpus signal the **P21 empirical calibration** thread (0.2.10+)
is built to collect.

## Decision for 0.2.9

**No weight or scoring change ships in 0.2.9.**

Per the roadmap's "ship the change only if the finding is unambiguous" gate, the
finding is *not* unambiguous: the file-line term is not a bug (it is not
redundant with complexity, and it tracks a real if coarse maintainability axis),
but it is also not clearly correct (it is a file-level property in a per-function
score, and it rewards cosmetic splits). Changing it on the strength of one
self-corpus run would be exactly the "re-tune weights based on vibes" move the
calibration thread exists to avoid.

Concrete outcomes:

1. **Ship this finding + the reproducible experiment** (`bin/experiments/
   sprawl_vs_complexity.py`, `data/calibration/sprawl-experiment.json`).
2. **Feed P21.** Add to the calibration agenda the specific question: do human
   reviewers accept split-driven sprawl drops as real? Candidate corrections to
   evaluate *with* corpus data, not before it:
   - drop the file-line term (sprawl = function length only);
   - shrink its share (e.g. 0.75 function / 0.25 file);
   - raise the file-line free/saturation band so only genuine god-modules move.
3. **Operational guidance now (no code change):** do not trust a baseline
   improvement that comes from a file split as a real maintainability gain.
   Working-rule #4 already reserves baseline regen for genuine refactors; a pure
   split that lowers scores via the file-line term is the case to be skeptical
   of. (The 0.2.9 release itself regenerated the baseline partly because adding
   CLI flags grew `cli.py` and raised the file-line term for its functions — a
   live instance of this very artifact, documented in the baseline bump
   rationale.)

## Reproducing

```bash
uv run python bin/experiments/sprawl_vs_complexity.py            # self corpus
uv run python bin/experiments/sprawl_vs_complexity.py path/to/other/src
```
