# Real coverage-producer fixtures (B2c, 0.3.0 validation gate)

These reports were produced by **running the actual tools**, not hand-authored. They close the
contract §2 pre-0.3.0 validation gate: before TS coverage feeds scoring, the parser
(`src/riskratchet/typescript_coverage.py`) is validated against real output from every supported
producer, since their `DA`/`BRDA` conventions genuinely differ.

## Source

Two tiny modules with the **same logic** — one branchy function (`classify`, exercised on the
positive path only, so the negative + zero arms stay uncovered) and one straight-line function
(`greet`, fully covered):

- `sample.js` — CommonJS, measured by c8/nyc/Jest/Vitest. `classify` spans lines 5-12, `greet` 14-16.
- `bsample.js` — browser globals (no `module.exports`), measured by Karma. `classify` spans lines
  1-8, `greet` 10-12.

## Producers and versions (Node v26.5.0)

| dir | tool | how | shape |
|---|---|---|---|
| `c8/` | c8@12.0.0 | `c8 --reporter=lcovonly --reporter=json node run.cjs` | **raw V8**: whole-file `DA`, single-arm `BRDA` per branch |
| `nyc/` | nyc@18.0.0 | `nyc --reporter=lcovonly --reporter=json node run.cjs` | Istanbul: statement-line `DA`, two-arm `BRDA` by `(line,block)` |
| `jest/` | jest@30.4.2 | `jest --coverage --coverageReporters=lcovonly` | Istanbul (byte-identical to nyc here) |
| `vitest/` | vitest@4.1.10 (+@vitest/coverage-v8@4.1.10) | `vitest run --coverage --coverage.provider=v8 --coverage.reporter=lcovonly` | Istanbul — the v8 provider **remaps** to Istanbul shape (not raw-V8 like c8) |
| `karma/` | karma@6.4.4 + karma-coverage@2.2.1, ChromeHeadless | `karma start` | Istanbul (karma-coverage instruments via istanbul) |

Istanbul-JSON (`coverage-final.json`) is captured for the two **distinct** JSON shapes only: `c8/`
(V8-derived) and `nyc/` (istanbul). Jest/Vitest/Karma JSON is the same istanbul shape as `nyc/`.

## What they prove

- The parser handles all five real byte shapes and maps them onto discovered spans.
- Real tools legitimately **diverge** on the same source: over `classify`, c8 reports line 0.62 /
  branch 0.50 (single-arm model), the Istanbul family reports 0.40 / 0.25 — so LCOV/Istanbul
  percentages across producers are **not interchangeable** (contract §2), which is exactly why TS
  coverage must be recalibrated before any cross-language scoring blend.
- Every producer emits `FN`/`FNDA` (function-level hit counts). Per the B2c decision, these stay a
  **cross-check only** (`fnda_called_functions` / `fn_declaration_lines`): the scored coverage
  fraction remains the producer-agnostic line-span reconstruction, because `FNDA` is binary
  called/not — it cannot express partial within-function coverage — and `FN`-name→span matching is
  fragile.

## Regenerate

`bin/calibration/`-style reproduction is not automated (it needs a Node toolchain + a browser for
Karma). To refresh: recreate `sample.js`/`bsample.js`/`run.cjs` + the per-tool test, run each tool as
in the table, and copy `lcov.info` / `coverage-final.json` back here. Keep the source line spans in
sync with this README and `tests/test_typescript_coverage_real_producers.py`.
