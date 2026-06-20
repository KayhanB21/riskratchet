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
| `generated.pb.ts` | generated code that must be **excluded** | none — file skipped (`@generated` header + `*.pb.ts` name) |

## Resolved decisions (were "open questions" in 0.2.11)

- **Inline callbacks** (`.map((x) => …)`, `useEffect(() => …)`): **skipped** — anonymous
  arrows with no const/let binding, matching how the Python backend ignores lambdas.
  Const/let-assigned arrows (incl. nested ones like `makeCounter.increment`) **are** kept.
- **Object-literal methods** (the `get` in `createClient`'s returned object): **skipped** —
  only `method_definition`s whose structural parent is a `class_body` count.
- **Interface/abstract method signatures**: **excluded** — they parse as `method_signature`,
  not function nodes, so they fall out naturally.
- **Generated-code detection**: an `@generated` header marker **or** a `*.pb.ts` / `*.gen.ts`
  filename excludes the whole file.

Still unsupported (silently skipped, documented for later slices): generator functions and
async iterators.
