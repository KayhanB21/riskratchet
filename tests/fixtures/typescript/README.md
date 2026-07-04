# TypeScript / TSX fixture corpus

Corpus for experimental TypeScript discovery. Added as a static spec in the groundwork
release (P19, `0.2.11`); **as of slice 2 (P20, `0.2.12`) these are exercised by real
assertions** in [`tests/test_typescript_discovery.py`](../../test_typescript_discovery.py).
The `.ts`/`.tsx` files are still ignored by pytest collection (it collects only
`test_*.py` / `*_test.py`) and by mypy (`tests/fixtures/` is excluded); the test module
reads them as data. They also back the contract in
[`docs/language-backend-contract.md`](../../../docs/language-backend-contract.md).

The table below is now **verified output**, not prediction — it is exactly what
`riskratchet.typescript.discover_typescript` returns (confirmed by the slice-2 spike, see
[`docs/typescript-parser-decision.md`](../../../docs/typescript-parser-decision.md)).

## Files and discovered functions (verified)

| File | Demonstrates | Discovered (qualname [visibility]) |
| --- | --- | --- |
| `top_level.ts` | top-level function declarations (sync + async) | `add` [pub], `greet` [int], `parseConfig` [pub, async] |
| `methods.ts` | class methods, getter, interface signatures | `Account.{constructor,deposit,withdraw,balance,record,total}` [all pub]; interface `Ledger` signatures **excluded** (no body) |
| `arrows.ts` | arrow functions, const-assigned, nested | `double` [pub], `clamp` [pub], `makeCounter` [pub], `makeCounter.increment` [int], `scaleAll` [pub]; inline `.map` callback **excluded** |
| `components.tsx` | React function components, hooks, JSX | `Greeting` [pub], `Counter` [pub], `Counter.handleClick` [int]; `useEffect`/`setState` callbacks **excluded** |
| `default_export.ts` | default export + internal helper | `createClient` [pub] (default export), `buildHeaders` [int]; object-literal `get` **excluded** |
| `namespaces.ts` | namespace members don't collide with top-level | `Foo.bar` [pub], `bar` [int] |
| `abstract.ts` | abstract class: concrete method kept, signature excluded | `Shape.concrete` [pub]; `abstract area()` **excluded** (no body) |
| `anonymous_default.ts` | anonymous `export default class` keeps a class segment | `default.m` [pub] |
| `reexport.ts` | export reachability via `export { … }` clauses | `Svc.run` [pub], `helper` [pub] (`as default`), `hidden` [int] |
| `async_variants.ts` | `is_async` across declaration / arrow / method | `loadOne`, `loadAll`, `Repo.deposit` — all [pub, async] |
| `internal_class.ts` | non-exported class method + `function_expression` | `Internal.helper` [int], `legacy` [int] (kind `function`) |
| `mts_module.mts` | `.mts` discovered like `.ts` | `fromMjs` [pub] |
| `broken.ts` | syntax error → skipped whole (warned), not partially listed | none — file skipped (`has_error`) |
| `generated.pb.ts` | generated code that must be **excluded** | none — file skipped (`@generated` header + `*.pb.ts` name) |
| `app/sample.ts` + `app/coverage-final.json` | slice-3 coverage mapping: discovered spans annotated from a **hand-authored** Istanbul report (no nyc run, so the suite stays hermetic) | `covered` 100% line / no branch; `partial` 80% line, 50% branch, miss-line 11 |
| `complexity_cases.ts` | slice-4 cyclomatic complexity, ESLint-aligned (`??` counted, `?.` not, `switch default` not, default params counted, nested pruned) | `straight` cx 1, `branchy` cx 7, `loopy` cx 8, `optionalChainAndNested` cx 2, `optionalChainAndNested.inner` cx 2, `withDefaults` cx 6 |
| `barrel/{index,public_api,helpers,internal}.ts` | slice-4 barrel-aware public surface: entry `index.ts` narrows file-exports to entry reachability | `exposed` [pub] (re-exported by name), `alsoExposed` [int] (not re-exported), `helper` [pub] (via `export *`), `hidden` [int] (unreferenced module) |

## Resolved decisions (were "open questions" in 0.2.11)

- **Inline callbacks** (`.map((x) => …)`, `useEffect(() => …)`): **skipped** — anonymous
  arrows with no const/let binding, matching how the Python backend ignores lambdas.
  Const/let-assigned arrows (incl. nested ones like `makeCounter.increment`) **are** kept.
- **Object-literal methods** (the `get` in `createClient`'s returned object): **skipped** —
  only `method_definition`s whose structural parent is a `class_body` count.
- **Interface/abstract method signatures**: **excluded** — they parse as `method_signature`,
  not function nodes, so they fall out naturally.
- **Generated-code detection**: a **comment-anchored** `@generated` header marker (`//`,
  `/*`, or a `*` continuation line — not `@generated` in a string or trailing code) **or** a
  `*.pb.ts` / `*.gen.ts` filename (incl. `.mts`/`.cts`) excludes the whole file.
- **Qualname scopes**: classes (named, `abstract`, and anonymous default-export), functions,
  and `namespace`/`module` blocks all contribute segments, so `Foo.bar` ≠ top-level `bar` and
  an anonymous default class's methods read as `default.m`.
- **Public surface**: export reachability — inline `export`/`export default` **and** separate
  `export { name }` / `export { name as default }` clauses; methods inherit their class. Since
  slice 4, a bare `export default <identifier>` is also recognized, and with an entry barrel the
  surface is **narrowed** to cross-file re-export reachability (see the `barrel/` fixtures).
- **Broken files**: a tree with `has_error` is skipped whole and reported, never partially
  listed.
- **Extensions**: `.ts`, `.tsx`, `.mts`, `.cts` are all discovered.

Still unsupported (silently skipped, documented for later slices): generator functions and
async iterators.
