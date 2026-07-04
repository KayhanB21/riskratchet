// Barrel entry (P20 slice 4, since 0.2.14). Reachability from this file defines the package
// public surface; tests/test_typescript_exports.py asserts the narrowing it drives.
//   - `exposed` is re-exported by name → stays public.
//   - `alsoExposed` (also in public_api.ts) is NOT re-exported → narrowed to internal.
//   - everything in helpers.ts is re-exported via `export *` → public.
//   - internal.ts is not referenced anywhere → its exports narrow to internal.
export { exposed } from "./public_api";
export * from "./helpers";
