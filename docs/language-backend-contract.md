# Language backend contract

`riskratchet` scores Python today, but the scoring, baseline, comparison, and
reporting pipeline downstream of *discovery* is language-neutral: it operates on
plain data (`FunctionRisk`, `RiskComponents`, `CoverageStats`), never on a Python
AST. This document names the contract a language backend must fill so a future
TypeScript backend (P19–P20, `0.2.13` →) can be slotted in without touching
scoring or output.

It is a **contract, not an implementation plan**. Each section states what the
engine needs, points at the Python reference, and notes the open questions for
TypeScript. No TypeScript code ships in `0.2.11` — see
[`typescript-parser-decision.md`](./typescript-parser-decision.md) for the parser
strategy.

## The seam

**This seam does not exist in the code yet.** Discovery is currently hard-coded to
Python — `analysis.py` calls `ast` directly; there is no backend interface to
implement against. Slice 2 (`0.2.13`) must first *carve* this seam out of
`analysis.py` (a refactor that extracts a backend protocol) before a TypeScript
backend can be plugged in. This document describes the contract that refactor should
expose, using today's Python code as the worked reference.

`engine.analyze()` (`src/riskratchet/engine.py`) is the single entry point. Once the
seam exists, a backend supplies five things per file and the engine hands pure data
to the downstream pipeline. A backend supplies:

1. **Function discovery** — which spans are functions, and their identity.
2. **Coverage mapping** — line/branch coverage per function span.
3. **Complexity** — a cyclomatic count per function.
4. **Public surface** — whether a function is part of the public API.
5. **Function identity** — a stable id plus body/signature fingerprints for
   rename-aware baseline matching.

Everything after that (`scoring.py`, `baseline/`, `reporting/`) is shared. The
first additive output hook for multi-language is the `function.language` field
(see "Output seam" below).

## 1. Function discovery

**Contract.** Given a source file, return the set of functions, each with a
`FunctionSpan(start_line, end_line)` and a dotted `qualname` reflecting nesting
(`Outer.inner`, `Class.method`). Files that fail to parse are skipped, not fatal.

**Python reference.** `src/riskratchet/analysis.py` — `parse_file()` runs
`ast.parse()`; `_FunctionCollector` (an `ast.NodeVisitor`) walks `FunctionDef` /
`AsyncFunctionDef`, building `qualname` from a context stack of enclosing classes
and functions. Methods and nested functions count; lambdas and comprehensions do
not (they have no independent identity). Parse failures return a `ParseError` the
engine skips.

**TypeScript notes / open questions.** What counts as a function is wider:
top-level `function` declarations, class methods, interface method *signatures*
(declaration-only — likely excluded, no body to score), arrow functions
(especially const-assigned `const f = () => {}` and inline callbacks), React
function components, IIFEs, and default-exported functions. Generated code (e.g.
`*.pb.ts`) should be excluded the way vendored/generated Python is. The TSX
fixture corpus under `tests/fixtures/typescript/` enumerates the cases the future
discovery is graded against.

## 2. Coverage mapping

**Contract.** Given a function span and the project's coverage data, return a
`CoverageStats(line_coverage, branch_coverage, missing_lines, missing_branches)`
— line coverage as the fraction of measured lines in the span that executed,
branch coverage as executed/total branches (or `None` if not measured). A
missing-file policy (`PESSIMISTIC` / `OPTIMISTIC` / `SKIP`) governs files absent
from the coverage report.

**Python reference.** `src/riskratchet/coverage.py` — `load_coverage()` parses
`coverage.py` JSON (`coverage json`) into exact- and suffix-indexed lookups;
`coverage_for_span()` intersects the span's `[start_line, end_line]` with the
file's executed/missing line and branch sets. The format is already
language-agnostic JSON; only the *producer* is Python-specific.

**TypeScript notes / open questions.** Map Istanbul/nyc/LCOV output to discovered
spans. Istanbul reports per-statement and per-branch coverage keyed by byte/line
ranges; the work is translating those ranges onto function spans and deriving the
same line/branch fractions. LCOV is line/branch oriented and closer to the
existing shape. This lands in slice 3 (`0.2.14`).

## 3. Complexity

**Contract.** Return a `ComplexityStats(cyclomatic)` per function — a McCabe
cyclomatic count, minimum 1, incremented at each branching point.

**Python reference.** `src/riskratchet/complexity.py` — primary path is
`radon.complexity.cc_visit_ast()`; `_manual_cyclomatic()` is the fallback for
functions radon rolls into a parent. The increment set: `if`/`if`-expression,
`for`/`async for`, `while`, `except`, `assert`, each non-first `and`/`or` clause,
each comprehension filter, each `match` case.

**TypeScript notes / open questions.** The algorithm is language-independent; the
work is enumerating the equivalent TS branching nodes (`if`, `for`, `for…of`,
`for…in`, `while`, `do`, `case`, `catch`, `?:`, `&&`/`||`/`??`, optional
chaining) over whichever parse tree the backend produces. Slice 4 (`0.2.15`).

## 4. Public surface

**Contract.** Return `is_public: bool` per function — is it part of the API a
consumer is meant to call, versus an internal helper.

**Python reference.** `src/riskratchet/analysis.py` — `_compute_is_public()`
combines the underscore convention (`_helper` private; dunders like `__init__`
public) with an optional `__all__` override (`_extract_dunder_all()` reads a
static `__all__` list/tuple and promotes listed top-level names). A nested
segment that is private keeps the whole function private.

**TypeScript notes / open questions.** The signal is `export` / `export default`
rather than naming convention; un-exported declarations are internal. React
components are public by convention *only if exported*. Re-exports and barrel
files (`index.ts`) complicate "what is the surface" — an open question for slice
4.

## 5. Function identity

**Contract.** Provide a stable `FunctionId(path, qualname)` (rendered
`path::qualname`) plus two fingerprints used by rename-aware baseline matching: a
**body fingerprint** (stable across rename and line shifts, changes on body
edits) and a **signature fingerprint** (stable across body edits, changes on
parameter/decorator changes).

**Python reference.** `src/riskratchet/analysis.py` `function_fingerprint()`
deep-clones the AST node, strips the name and all source locations, and hashes the
`ast.dump()` (SHA-256). `src/riskratchet/matching.py` `signature_fingerprint()`
clears name and body, keeping args/decorators/return annotation. `match_rename()`
scores candidates with the weights `(BODY 0.55, SIG 0.20, PATH 0.10,
QUALNAME_TAIL 0.05, COMPONENT 0.05, SCORE 0.05)`, threshold `0.65`.

**TypeScript notes / open questions.** A backend needs a **token-stable**
serialization of a TS function body and signature — equivalent to "strip names
and source positions, hash the structure." The matcher weights and threshold are
language-neutral and should not need re-tuning, but the fingerprints must be
stable across the TS formatter's whitespace/quote choices.

## Output seam

The pipeline downstream of discovery operates on plain data (`FunctionRisk`, not an
AST), so it is **structurally ready** to be language-neutral once discovery is
abstracted — that abstraction is the slice-2 refactor above, not something that
exists today. The first additive multi-language hook shipped in `0.2.11`:

- `FunctionRisk.language` (`src/riskratchet/models.py`) — defaults to `"python"`.
- Emitted as `function.language` in `scan --json` and `explain --json` via the
  shared `_function_payload()` (`src/riskratchet/reporting/json_payload.py`).
- Declared in `schemas/report.schema.json` and `schemas/explain.schema.json` as
  `{ "const": "python" }` today; a future backend relaxes this to
  `{ "enum": ["python", "typescript"] }`.

The field is additive: it is always `"python"` until a real backend sets
otherwise, so existing consumers are unaffected. The baseline format, SARIF, and
the check/diff payloads do **not** carry `language` yet — they gain it only when
TypeScript scoring actually ships (slice 5, `0.2.16`).

## Non-goals for groundwork

- No TypeScript scoring, discovery, or parser dependency ships in `0.2.11`.
- No change to Python scoring, weights, thresholds, or the matcher.
- No mandatory Node dependency for Python-only installs — ever (see the parser
  decision doc).
