# TypeScript parser decision

Groundwork decision (P19, `0.2.11`) for how a future TypeScript backend will
parse `.ts` / `.tsx`. No parser ships in `0.2.11`; this records the chosen
strategy and the rejected alternatives so slice 2 (`0.2.13`) starts from a
settled position. The constraint that drives the decision: **Python-only installs
must stay unchanged** — no new runtime dependency, and absolutely no Node runtime
forced on users who only scan Python.

## Decision

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
| **tree-sitter** (chosen) | High (structural) | Native wheel, optional extra | None (lazy, opt-in) | Yes (`tsx` grammar) | **Chosen** |
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
