# TypeScript / TSX fixture corpus

Static corpus for the TypeScript groundwork (P19, `0.2.11`). These `.ts` / `.tsx`
files are **checked in but not exercised by any gate test** — pytest collects only
`test_*.py` / `*_test.py`, and mypy excludes `tests/fixtures/`. They are the spec
the future TypeScript function discovery (slice 2, `0.2.13`) is graded against, and
worked examples for the cases named in
[`docs/language-backend-contract.md`](../../../docs/language-backend-contract.md).

No scoring or discovery runs over these yet. When slice 2 lands, the "expected
discovery" notes below become real assertions.

## Files and expected discovery

| File | Demonstrates | Expected functions |
| --- | --- | --- |
| `top_level.ts` | top-level function declarations (sync + async) | `add`, `greet`, `parseConfig` |
| `methods.ts` | class methods, getter, interface method signatures | `Account.constructor`, `Account.deposit`, `Account.withdraw`, `Account.balance`, `Account.record`, `Account.total`; interface `Ledger` signatures **excluded** (no body) |
| `arrows.ts` | arrow functions, const-assigned, nested | `double`, `clamp`, `makeCounter`, nested `increment`, `scaleAll`; inline `.map` callback is an open question |
| `components.tsx` | React function components, hooks, JSX | `Greeting`, `Counter`, nested `handleClick`; `useEffect` callback is an open question |
| `default_export.ts` | default export + internal helper | `createClient` (default, public), `buildHeaders` (internal) |
| `generated.pb.ts` | generated code that must be **excluded** | none — file skipped by the generated-code heuristic |

## Open questions for slice 2

- **Inline callbacks** (`.map((x) => …)`, `useEffect(() => …)`): count as functions
  or skip? They have no independent name/identity; leaning skip, matching how Python
  ignores lambdas.
- **Interface/abstract method signatures**: declaration-only, no body to score —
  expected excluded.
- **Generated-code detection**: header marker (`@generated`) vs filename pattern
  (`*.pb.ts`, `*.gen.ts`) vs config `exclude`. The Python backend leans on `exclude`;
  the heuristic here is a starting point.
