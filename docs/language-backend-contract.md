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

**The discovered-function shapes are now unified behind one protocol;
identity is the remaining gap.** Python discovery is in `analysis.py` (it calls `ast`
directly) and TypeScript discovery in a *separate* module, `typescript.py` (tree-sitter),
reached only through `scan --experimental-typescript`. They share the language-neutral
`FunctionId`/`FunctionSpan` data shapes and the path helpers in `riskratchet._paths`
(`relative_posix`, `has_hidden_parent`, `any_match`), and **now a common protocol**:
`models.DiscoveredFunctionLike` (`id`, `span`, `is_public`, `is_async`). Both
`analysis.DiscoveredFunction` and `typescript.TsFunction` conform to it — checked statically
(mypy) and at runtime in `tests/test_backend_protocol.py`, so the two can't silently drift on
the common surface. Backend-agnostic code can be written against the protocol instead of a
concrete type.

What the protocol deliberately omits is **identity** — a token-stable body/signature
fingerprint for rename-aware baseline matching. Python supplies it on `DiscoveredFunction`;
tree-sitter discovery does not yet produce it. So while the engine *could* score either
language through the seam on the common surface, TypeScript cannot enter the
scoring/**baseline** pipeline until that identity half lands (the remaining slice-5 work,
tracked on `TsFunction`). The rest of this document describes the rest of the contract
(coverage, complexity, public surface, identity), using today's Python code as the worked
reference and the TS module as the second concrete data point.

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

**TypeScript status — slice 3 (`0.2.13`) landed for Istanbul JSON; LCOV added in `0.2.16`.**
`src/riskratchet/typescript_coverage.py` maps an Istanbul/nyc `coverage-final.json`
onto the spans `typescript.py` discovers, returning the same `CoverageStats`.
Algorithm: **line coverage** keys on each statement's `start.line` only (not its
end line), collapsing statements that share a line with `max` hit count — exactly
`istanbul-lib-coverage.getLineCoverage`; **branch coverage** counts the arms of each
branch whose `loc.start.line` falls in the span (`b[id]` is a per-arm hit-count array
positionally aligned to `branchMap[id].locations`), and `missing_branches` reuses the
`tuple[(int, int), …]` field as `(branch_line, arm_index)` — a TS-specific shape, since
Istanbul has no `(src_line, dst_line)` analog. Paths are matched basename + longest-suffix
(Istanbul keys are absolute). It is reached only through `scan
--experimental-typescript --ts-coverage` (repeatable — one report per package in a
monorepo, merged; Istanbul keys are absolute, so no prefix map is needed) and stays
informational (no scoring/gating).

**LCOV (`lcov.info`) is supported since `0.2.16`** — rather than teach the mapping a second
shape, an LCOV report is parsed into the *same* synthetic Istanbul-shaped per-file dict: each
`DA:<line>,<hits>` becomes a one-line statement, and each `BRDA:` group (keyed by `(line, block)`)
becomes a synthetic branch, so `coverage_for_ts_span` / `spans_cover_any_statement` and the
merge/lookup machinery are reused unchanged (an LCOV and an Istanbul report describing the same
measured lines yield identical `CoverageStats`). Format is auto-detected per file (extension
`.info`/`.lcov` or a leading `TN:`/`SF:` line → LCOV; a leading `{` → Istanbul JSON), so one
`--ts-coverage` list may mix both. LCOV `FN`/`FNDA` (function hit counts) and the `LF`/`LH`/`BRF`/
`BRH` file totals have no home in `CoverageStats` and are parsed-and-ignored.

**Semantics are not identical across backends — do not treat the percentages as
interchangeable.** TS `line_coverage` from Istanbul is *statement-start-derived* (a line counts as
measured iff an Istanbul statement starts on it); **LCOV is *line*-derived** (a line counts iff it
has a `DA` record) — a third measurement basis; the Python backend's is *line-level*
(coverage.py's executable-line set). Two functions both at "80%" across any of these are
not the same denominator, so this output is **not** consumable unchanged by a future
cross-language scoring blend — it must be recalibrated first. Likewise TS branch arms go in
`CoverageStats.missing_branch_arms` (`(line, arm_index)`), never the Python `missing_branches`
(`(src_line, dst_line)`).

**Source-map alignment is the load-bearing assumption.** The mapping trusts the report's
line numbers. If coverage was collected on *compiled JS* (c8/V8, or nyc instrumenting built
output without `babel-plugin-istanbul`) and not remapped back to `.ts`, those numbers describe
JS, not the source we parse. `spans_cover_any_statement` detects the gross case (a file whose
statements land in *no* discovered span) so the CLI warns and omits coverage for that file
rather than emitting wrong numbers; subtler partial misalignment is still possible and is why
this stays informational.

## 3. Complexity

**Contract.** Return a `ComplexityStats(cyclomatic)` per function — a McCabe
cyclomatic count, minimum 1, incremented at each branching point.

**Python reference.** `src/riskratchet/complexity.py` — primary path is
`radon.complexity.cc_visit_ast()`; `_manual_cyclomatic()` is the fallback for
functions radon rolls into a parent. The increment set: `if`/`if`-expression,
`for`/`async for`, `while`, `except`, `assert`, each non-first `and`/`or` clause,
each comprehension filter, each `match` case.

**TypeScript — implemented in slice 4 (`0.2.14`), aligned to ESLint's `complexity` rule.**
`src/riskratchet/typescript_complexity.py` (`cyclomatic_for_node`) walks the tree-sitter tree:
base 1, `+1` per `if_statement`, `ternary_expression`, `for_statement`, `for_in_statement`
(covers `for…of`/`for…in`), `while_statement`, `do_statement`, `catch_clause`, `switch_case`,
each `&&`/`||`/`??` in a `binary_expression`, and each **default parameter** (a
`required`/`optional_parameter` with a default value, plus each destructuring default
`object_assignment_pattern` / `assignment_pattern` — ESLint's `AssignmentPattern`). Computed at
discovery from the live node (no node retained), stored on `TsFunction.complexity`. Decisions,
all matching ESLint:

- **`switch_default` is not counted** (the catch-all is not a decision point).
- **`??` IS counted** (ESLint counts it — a `LogicalExpression`); **optional chaining `?.` is
  NOT** (ESLint does not count it either).
- **Nested functions are pruned** — each function is scored on its own, as ESLint scores each.

**Divergences from the Python reference (intentional, informational-only until slice 5).** The
Python `_manual_cyclomatic` (a) does **not** count default parameters and (b) does **not** prune
nested functions (`ast.walk` descends into them). So the raw TS and Python cyclomatic counts are
**not directly comparable** for those two shapes, and — importantly — the gap is *not a constant
offset*: default parameters push TS higher, absorbed nested functions push Python higher, so
which backend reads higher depends on the code shape. No scoring/baseline/gating consumes either
count today.

**Committed reconciliation for slice 5 — per-language normalization, not one shared rule.** When
TS enters scoring, the fix is to keep each backend's *raw* count as-is (TS stays ESLint-faithful
so the displayed `cx N` matches what a TS dev's linter shows) and instead give the
`structural_complexity` *normalization* its own per-backend calibration, so that the normalized
0–100 component represents the **same distribution position** regardless of language. Python
currently normalizes with `scoring._saturate(cc, free=1, saturation=21)`
(`COMPLEXITY_SATURATION_CC = 20`); TS gets its own `(free, saturation)` (or mapping). The
displayed metric and the scored metric are deliberately **decoupled**: ESLint-faithful on screen,
language-fair at the gate.

The TS constant is **derived, not hand-picked** — folded into the P21 calibration thread: run
both analyzers over comparable corpora, compare the per-function cyclomatic distributions, and set
the TS saturation so equal percentiles map to equal normalized scores. It ships at slice 5 (or
`0.3.0` if it also revisits the Python constant) with the corpus + rationale, never as a silent
number. This mirrors the coverage caveat in §2 (TS statement-derived vs Python line-level
coverage): in both, the *shape* is shared but the *measurement* is not, so both feed one scoring
model only after a data-anchored recalibration. Tradeoff accepted: two calibration surfaces
instead of one shared counting rule, plus a TS corpus study that does not exist yet — the cost of
not forcing one language's rules onto the other.

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
components are public by convention *only if exported*. As of the slice-2 discovery
module this is **export reachability**: a declaration is public if inline-exported *or*
named in a top-level `export { name }` / `export { name as default }` clause, and a
method inherits its (possibly clause-exported) class's surface unless it is
`private`/`protected`/`#name`.

**Barrel-aware narrowing — added in slice 4 (`0.2.14`).** File-level export is refined to
**package-entry reachability**: `src/riskratchet/typescript_exports.py` walks the re-export
graph (`export { x } from './m'`, `export { x as y } from`, `export * from`, transitively)
from the package entry and a file-exported function *not* reachable from an entry is narrowed to
internal. The entry is resolved in priority order: explicit `--ts-entry`, else `package.json`
(`exports`/`module`/`main`/`types` — **best-effort: only the fields that point at *source*
`.ts`; a built package whose `main`/`module` point at `dist/*.js` falls through**), else the
shallowest `index.{ts,tsx,mts,cts}`. The driving entry is announced on stderr (override with
`--ts-entry`). Also fixed here: a bare `export default myFunc;` referencing a separately-declared
binding (previously missed) now marks that binding public.

**Safety rail (per-name, not all-or-nothing).** Narrowing only *demotes*, and never on an
unproven graph. The guard is graded by edge kind: an **unresolved wildcard** (`export * from`
an unresolvable target) or a missing entry poisons the whole surface (a `*` could alias-expose
any name → demote nobody); an **unresolved named** re-export (`export { x } from 'pkg'`) holds
only that one name public and narrows everything else — so a single third-party named re-export
in a barrel no longer disables the feature. Rationale: an unresolved specifier points outside the
scanned set, and the only way it could hide one of *our* functions is a tsconfig alias back in —
a named alias exposes exactly one name (covered), a wildcard could expose anything (poisons).
Still out of scope (needs the type checker): tsconfig `paths`/`baseUrl` aliases, dynamic
`import()`, and **declaration merging**.

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

**TypeScript — implemented, unscored (slice 5, since 0.2.15).**
`src/riskratchet/typescript_identity.py` provides a token-stable serialization **analogous to** the
note above. `body_fingerprint()` serializes the whole function node (signature and body) with the
function's own name excluded; `signature_fingerprint()` does the same with the body block excluded
too — the same two-fingerprint split and SHA-256 `str` shape as Python's `function_fingerprint` /
`signature_fingerprint`, so it is **intended** to slot into `match_rename` when TS enters scoring.
It is **not** a faithful port of `ast.dump`: it is a lossy, hand-curated projection (walk the named
nodes, add back a small operator/modifier allowlist), so its completeness is unproven —
`tests/test_typescript_identity.py` carries a pairwise-distinctness battery as the guard, not a
proof.

Stability comes from serializing only *named* tree-sitter nodes: anonymous punctuation
(`{ } ( ) , ; : . =>`) and string/template quotes are dropped, so the hash is immune to
brace/spacing style, optional semicolons (ASI), trailing commas, and single-vs-double quotes;
`parenthesized_expression` is unwrapped so redundant parens don't count. Three classes of
*semantic* tokens that are anonymous in the grammar are added back explicitly, else they'd collide:
operators on `binary`/`unary`/`update`/`augmented_assignment` expressions, and (only on
function-like nodes, so the generator `*` never collides with the multiply operator) the modifier
keywords `async`/`get`/`set`/`static`/`*`. Modifier capture runs at every function-like node, so a
parent's body fingerprint reflects a nested function's modifiers.

**Durability requirement for 0.3.0.** The payload embeds `SCHEME_VERSION` (bump on any serializer
change) but **not** the tree-sitter-typescript **grammar version**, which the hash also depends on —
it serializes grammar node-type strings, so a grammar upgrade can silently change every fingerprint.
Harmless while the fingerprints are unconsumed (the matcher stays **unused** for TS this release —
identity is groundwork, carried but not yet scored/gated). But **before a baseline persists TS
fingerprints at 0.3.0**, that baseline must record the grammar + `SCHEME_VERSION`, and the grammar
must be pinned or version-gated so a bump is detected, not silently treated as a mass rename. The
matcher weights and threshold are language-neutral and are not re-tuned.

## Output seam

The pipeline downstream of discovery operates on plain data (`FunctionRisk`, not an
AST), so it is **structurally ready** to be language-neutral once discovery is
abstracted — that abstraction is the slice-2 refactor above, not something that
exists today. The first additive multi-language hook shipped in `0.2.11`:

- `FunctionRisk.language` (`src/riskratchet/models.py`) — defaults to `"python"`.
- Emitted as `function.language` in `scan --json` and `explain --json` via the
  shared `_function_payload()` (`src/riskratchet/reporting/json_payload.py`).
- Declared in `schemas/report.schema.json` and `schemas/explain.schema.json`.
  **Since 0.2.15 (slice 5)** this is `{ "enum": ["python", "typescript"] }` (was
  `{ "const": "python" }`).

Slice 5 wired TypeScript into the machine-readable output, **still unscored**:

- `scan --json --experimental-typescript` adds a top-level `typescript[]` array of
  unscored functions (`$defs/ts_function`): `path`, `qualname`,
  `language: "typescript"`, `kind`, `is_public`, `complexity`, line/branch coverage,
  `lines`, and the identity `fingerprint`/`signature`. No `score`/`components` —
  TypeScript is informational until `0.3.0`. The key is **omitted** without the flag,
  so the Python contract and every snapshot are byte-stable.
- `scan --format sarif --experimental-typescript` emits each TS function as an
  informational `level: "note"` result under the new `riskratchet.typescript-function`
  rule (registered only when TS results are present), tagged `language: "typescript"`.
- Also fixed a latent gap: the scored Python SARIF result `properties` now carry
  `language` and `group` (the JSON payload already had both since 0.2.11 / group
  support). The baseline format and the check/diff payloads still do **not** carry
  `language` — they gain it only when TypeScript scoring ships (`0.3.0`).

## Non-goals for groundwork

- No TypeScript scoring, discovery, or parser dependency ships in `0.2.11`.
- No change to Python scoring, weights, thresholds, or the matcher.
- No mandatory Node dependency for Python-only installs — ever (see the parser
  decision doc).
