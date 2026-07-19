# TypeScript parser decision

Groundwork recommendation (P19, `0.2.11`) for how a future TypeScript backend will
parse `.ts` / `.tsx`. No parser ships in `0.2.11`; this records the recommended
strategy and the rejected alternatives so slice 2 (`0.2.13`) starts from a
considered position. The constraint that drives the recommendation: **Python-only
installs must stay unchanged** — no new runtime dependency, and absolutely no Node
runtime forced on users who only scan Python.

## Decision: tree-sitter (spike confirmed, 0.2.12)

**The slice-2 spike ran and tree-sitter passed.** In `0.2.12` (slice 2 was pulled forward
from `0.2.13`), `tree-sitter-typescript` was run against the full
`tests/fixtures/typescript/` corpus and expressed every discovery contract area cleanly —
so the paper recommendation below is now the committed choice. What the spike confirmed:

- **Function nodes + spans.** `function_declaration`, `method_definition` (incl.
  constructors and getters), and `arrow_function`/`function_expression` all parse with
  exact line spans; all six fixtures parse with `has_error = False`.
- **Qualnames.** Built by walking ancestor scopes — `class_declaration`,
  `abstract_class_declaration`, anonymous class *expressions* (`class`),
  `function_declaration`, function-valued `variable_declarator`, and `namespace`/`module`
  blocks (`internal_module` / `module`) — yielding `Account.deposit`, `makeCounter.increment`,
  `Counter.handleClick`, and `Foo.bar` for namespaced members (which therefore do not collide
  with a top-level `bar`). Anonymous default-export classes contribute a `default` segment so
  their methods (`default.m`) don't silently merge into the file's top level.
- **`export`-based public surface.** Exported declarations sit under an `export_statement`
  ancestor; non-exported ones don't — a clean public/internal signal without naming
  conventions. Reachability also covers separate `export { name }` / `export { name as default }`
  clauses, not only inline `export`, so a class exported after its declaration (and its
  methods) reads as public.
- **Error tolerance, surfaced.** tree-sitter returns a usable tree on broken input, but a tree
  with `root_node.has_error` is skipped whole (with a warning), matching the Python backend's
  skip-unparseable-files behaviour — partial/garbage results are never emitted.
- **Exclusions.** Anonymous inline callbacks surface as `arrow_function` with a non-binding
  parent (`arguments`), object-literal methods as `method_definition` under `object` (vs
  `class_body`), and interface method *signatures* as non-function `method_signature` nodes
  — each cleanly filterable. Generated files are caught by `@generated` header / `*.pb.ts`
  name.

Coverage mapping (slice 3, `0.2.13`) has since landed for Istanbul JSON
(`typescript_coverage.py`; see `language-backend-contract.md §2`), and **slice 4 (`0.2.14`)**
added cyclomatic complexity (`typescript_complexity.py`) and barrel-aware public-surface
narrowing (`typescript_exports.py`) — both over the same tree-sitter tree, still no Node-backed
fallback. Since then, coverage gained **LCOV** support (`0.2.16`) and slice 5 (`0.2.15`) added the token-stable
body/signature **identity fingerprints** (`typescript_identity.py`, still informational). The contract
areas still **not** exercised (deferred, not blockers) are **declaration merging** (the one
public-surface case that genuinely needs the type checker) and tsconfig `paths`/`baseUrl` alias
resolution.

The recommendation, now committed:

Use **tree-sitter** (`tree-sitter` + `tree-sitter-typescript` Python bindings),
shipped as an **optional extra** — `pip install riskratchet[typescript]`. The
binding is a small native wheel with no Node runtime; it stays out of the default
dependency set, so a Python-only install resolves exactly as it does today. The
TypeScript code path imports the parser lazily and errors with a clear "install
`riskratchet[typescript]`" message if invoked without the extra.

Why tree-sitter:

- **No Node runtime.** Pure native wheels; nothing in the user's `PATH` required.
- **TSX/JSX support.** `tree-sitter-typescript` ships a dedicated `tsx` grammar.
- **Error-tolerant.** Produces a usable tree on partially-invalid input, matching
  how the Python backend skips unparseable files rather than failing the run.
- **Span-oriented.** Node start/end byte+row positions map directly onto the
  `FunctionSpan` and coverage-mapping contract.
- **Already the roadmap's lean** (`docs/riskratchet-0.2x-roadmap.md` §0.2.11
  Risks): evaluate tree-sitter first, Node only as a fallback.

## Alternatives considered

| Option | Accuracy | Packaging | Python-only impact | TSX/JSX | Verdict |
| --- | --- | --- | --- | --- | --- |
| **tree-sitter** (recommended) | High (structural) | Native wheel, optional extra | None (lazy, opt-in) | Yes (`tsx` grammar) | **Recommended** (pending spike) |
| Node-backed (TS compiler API) | Highest (full type info) | Requires Node runtime | Heavy — Node on every TS user | Yes | Rejected; fallback only |
| Regex / heuristic | Low | None | None | Fragile | Rejected |

**Node-backed (TypeScript compiler API via subprocess or embedded Node).** The
most accurate option — it is the real TS parser with full type resolution. But it
forces a Node runtime and an `npm` dependency tree on every TypeScript user, a
heavy and fragile packaging story for a Python-distributed tool. Kept only as a
**fallback** if tree-sitter cannot express something we need (e.g. type-aware
public-surface inference). Not the day-one path.

**Regex / heuristic discovery.** Cheap and dependency-free, but TS/TSX syntax
(arrow functions, generics, JSX, decorators, overloads) is far past what regex can
reliably parse. It would produce wrong spans and wrong identity fingerprints —
unacceptable for a baseline-gating tool. Rejected.

## Non-goals

- **No source-map walking.** TypeScript sources are scanned as written, not as
  compiled to JS.
- **No framework conventions beyond React** on day one. Vue/Angular/Svelte only
  with documented demand.
- **No generators / async iterators** guaranteed in the first discovery slice;
  document them as explicitly unsupported until implemented.
- **Limited type-aware analysis.** tree-sitter is syntactic. Slice 4 (`0.2.14`) resolves
  *re-exported* public surface syntactically — a re-export graph over the scanned files with a
  conservative "never demote on an unresolved chain" fallback (`typescript_exports.py`), so it
  did **not** reopen the Node-backed fallback. What still genuinely needs the type checker —
  **declaration merging**, and tsconfig `paths`/`baseUrl` alias resolution — stays deferred and
  remains the only scenario that would.

## Packaging note

The extra is declared as `[project.optional-dependencies].typescript` (landed in
`0.2.12`): `tree-sitter>=0.23,<0.26` and `tree-sitter-typescript>=0.23,<0.24`. The
upper bounds are deliberate — discovery asserts against the grammar's exact node
taxonomy, so a major `tree-sitter-typescript` bump could rename/restructure nodes and
silently break the suite via an unrelated `uv lock` refresh; the ceiling forces a
deliberate, test-gated upgrade. The `0.2.11` wheel had the same runtime dependency set
as `0.2.10`; the `0.2.12` wheel keeps tree-sitter behind the `extra == "typescript"`
marker, so a Python-only install still resolves unchanged (`uv export --no-dev` shows no
tree-sitter; `uv build --clear` confirms the `Requires-Dist` gating).
