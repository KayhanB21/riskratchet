# P24: sprawl-component validation finding (0.2.9)

## Question

Every `0.2.x` module split (reporting in 0.2.6, baseline + config in 0.2.7)
scored "better" partly because moving functions into smaller files lowers their
`sprawl` component. The roadmap asks: does the per-file `sprawl` component
measure anything the per-function `structural_complexity` component does not —
i.e. **should** splitting a module into smaller files move scores at all?

Reproduce:

```bash
uv run python bin/experiments/sprawl_vs_complexity.py --clone
# rolled-up results -> data/calibration/sprawl-experiment.json
```

> **Status: preliminary.** This measures inter-metric correlation on a
> 4-repository corpus. It does **not** measure predictive validity (whether
> sprawl predicts defects, review time, or churn). Only labelled outcomes
> settle "should it move scores," which is the P21 calibration thread's job.
> This finding scopes the question and rules some answers in/out; it does not
> close it.

## How sprawl is built

```python
sprawl = (function_line_score + file_line_score) / 2          # scoring.py:94
function_line_score = saturate(function.line_count, free=80,  saturation=160)
file_line_score     = saturate(file.total_lines,    free=500, saturation=1000)
```

sprawl blends a **per-function** term (function length) with a **per-file** term
(file length). It is 10% of the total score. The file-line term is identical for
every function in a file — a file-level property in a per-function score — and is
exactly what a module split changes.

## Prior art (which I should have engaged the first time)

- **El Emam, Benlarbi, Goel & Rai (2001), "The Confounding Effect of Class Size
  on the Validity of Object-Oriented Metrics" (IEEE TSE).** The load-bearing
  reference: module/class *size* confounds most OO metrics; once you control for
  size, much of their apparent validity disappears. This is precisely the shape
  of the sprawl file-line term — a size signal injected into a per-function
  score — and the reason to be suspicious of treating it as independent risk.
- **Fenton & Neil (1999), "A Critique of Software Defect Prediction Models"
  (IEEE TSE).** Size correlates with raw defect *counts* largely because bigger
  units contain more code; size is a weak basis for normalized risk. Cautions
  against reading "bigger ⇒ riskier per function" as causal.
- **Lanza & Marinescu (2006), *Object-Oriented Metrics in Practice*.** The
  countervailing view: God Class / Large Class are real smells with useful size
  thresholds. There *is* a regime (genuine god-modules) where file size is a
  legitimate maintainability signal — which is why "drop sprawl entirely" is not
  obviously right.
- **McCabe (1976)** for cyclomatic complexity (what `structural_complexity`
  measures) and the **CRAP** metric (Savoia/Alberg) that riskratchet also
  reports — both per-function, size-independent by construction.

The literature does not settle our question; it frames it. El Emam says "expect
size to confound"; Lanza & Marinescu say "size still matters past a threshold."
That tension is exactly what corpus outcome data (P21) must resolve.

## Evidence

### 1. Across a real multi-repo corpus, sprawl ≈ a file-level signal, near-orthogonal to complexity

Pooled over **3,942 functions in 4 repositories** (riskratchet, `requests`,
`httpx`, `rich`); Spearman is primary because the components are clamped,
saturated, and zero-inflated (Pearson understates monotonic association):

| pair | Pearson | Spearman |
| --- | ---: | ---: |
| `sprawl` vs `structural_complexity` | 0.088 | **0.071** |
| `file_line_term` vs `structural_complexity` | 0.043 | **0.058** |
| `function_line_term` vs `structural_complexity` | 0.352 | 0.149 |

Per repo, `sprawl`↔`structural` Spearman ranged 0.05–0.28 (riskratchet's own
0.28 was the **high outlier** — the single-repo number the first pass leaned on).
Pooled, sprawl and complexity are essentially uncorrelated.

**The hypothesis "sprawl just re-measures complexity" is rejected** — but not in
sprawl's favour. It measures a *different* thing (size), and the dominant half is
a file-level property nearly independent of the function.

### 2. In practice, sprawl IS the file-line term

Pooled distributions (share of functions scoring exactly 0):

| term | median | p75 | zeros |
| --- | ---: | ---: | ---: |
| `function_line_term` | 0.0 | 0.0 | **98.9%** |
| `file_line_term` | 3.2 | 100.0 | 48.9% |
| `sprawl` (their mean) | 1.8 | 50.0 | 48.6% |

The function-length half fires for **~1% of functions** (almost nothing is over
80 lines). So for nearly every function, `sprawl == file_line_term / 2`: the
component is, in practice, "how big is the file you live in," halved. Two
functions identical in body, complexity, and coverage receive different risk
solely from sibling count.

### 3. Splitting a file mechanically lowers every function in it

Synthetic control — identical 40-line, CC=8 function in files of increasing size:

| file lines | sprawl | structural | total score |
| ---: | ---: | ---: | ---: |
| 300 | 0.0 | 35.0 | 36.25 |
| 600 | 10.0 | 35.0 | 37.25 |
| 1200 | 50.0 | 35.0 | **41.25** |

A +5.0 swing on file size alone (`0.10 weight × 0.5 × 100`), enough to cross a
25-point severity band. The real split simulation (halving `config.py`,
627→313 lines, which straddles the 500–1000 band) drops its functions by up to
1.3 points with **no body change** — smaller than the synthetic ceiling because
the saturating band caps the effect, but real and one-directional.

## Interpretation

Two defensible readings, now with literature attached:

- **El Emam / Fenton-Neil reading:** the file-line term is a size confound. It is
  near-orthogonal to complexity, fires file-wide, and rewards cosmetic splits —
  the hallmarks of a size artifact masquerading as per-function risk.
- **Lanza-Marinescu reading:** file size past a threshold is a genuine
  maintainability axis (God Module), and the saturating 500–1000 band is a crude
  but real proxy for it. Under this reading a split that shrinks a god-module *is*
  a real improvement.

The corpus data is consistent with both: it shows sprawl is a distinct,
file-level, size-driven signal (not redundant with complexity), but it cannot say
whether that signal *predicts maintenance pain*. That requires labelled outcomes.

## Decision for 0.2.9

**No weight or scoring change ships in 0.2.9** (unchanged from the first pass, now
better justified).

The finding is sharper but still not unambiguous: the file-line term is a size
signal (El Emam-style confound) that fires file-wide and rewards cosmetic
splits — yet a defensible god-module reading survives, and we have no labelled
outcome data to choose. Retuning on inter-metric correlation alone would be the
"tune on vibes" move the calibration thread exists to prevent.

Concrete outcomes:

1. **Ship this finding + the reproducible multi-repo experiment.**
2. **Feed P21 a specific, testable question:** do human reviewers accept
   split-driven sprawl drops as real improvements? Candidate corrections to
   evaluate *against outcomes*, ruled in by this data:
   - drop the file-line term (sprawl = function length only) — note this would
     make sprawl ~0 for 99% of functions, i.e. nearly delete the component;
   - shrink the file-line share (e.g. 0.75 function / 0.25 file);
   - raise the 500/1000 band so only true god-modules move (the Lanza-Marinescu
     threshold reading).
3. **Operational guidance now (no code change):** do not treat a baseline
   improvement that comes from a file split as a real maintainability gain.
   The 0.2.9 release itself regenerated its baseline partly because adding CLI
   flags grew `cli.py` and lifted the file-line term for its functions — a live
   instance of this artifact, recorded in the baseline bump rationale.

## Honest limitations

- 4 repos, all mainstream Python libraries — not generalizable to applications,
  notebooks, or other languages.
- Inter-metric correlation ≠ predictive validity; no defect/review labels here.
- The split simulation halves line counts; it does not model what splitting does
  to coupling or import graphs (which a real refactor changes and this metric
  ignores entirely).
