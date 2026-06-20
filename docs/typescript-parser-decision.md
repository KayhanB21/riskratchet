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
- **Qualnames.** Built by walking ancestor scopes (`class_declaration` /
  `function_declaration` names and function-valued `variable_declarator` names), yielding
  `Account.deposit`, `makeCounter.increment`, `Counter.handleClick`.
- **`export`-based public surface.** Exported declarations sit under an `export_statement`
  ancestor; non-exported ones don't — a clean public/internal signal without naming
  conventions.
- **Exclusions.** Anonymous inline callbacks surface as `arrow_function` with a non-binding
  parent (`arguments`), object-literal methods as `method_definition` under `object` (vs
  `class_body`), and interface method *signatures* as non-function `method_signature` nodes
  — each cleanly filterable. Generated files are caught by `@generated` header / `*.pb.ts`
  name.

The only contract areas **not** exercised yet (deferred to later slices, not blockers for
discovery): coverage mapping (slice 3), cyclomatic complexity (slice 4), and the
token-stable signature fingerprint for rename matching (only needed once TS enters the
baseline). No Node-backed fallback was required.

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
- **No type-aware analysis.** tree-sitter is syntactic; anything needing the type
  checker (e.g. resolving re-exported public surface) is deferred and is the only
  scenario that would reopen the Node-backed fallback.

## Packaging note

The extra is declared as `[project.optional-dependencies].typescript` when slice
2 lands. Until then, no dependency is added — `0.2.11` ships docs and fixtures
only. The wheel built in `0.2.11` has the same runtime dependency set as
`0.2.10`; `uv build --clear` confirms it.
