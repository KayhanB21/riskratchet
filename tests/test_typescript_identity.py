"""Token-stable identity for discovered TypeScript functions (P20 slice 5, since 0.2.15).

tree-sitter lives in the optional `typescript` extra, so this module skips when it is absent.
The contract under test is analogous to the Python backend (`analysis.function_fingerprint` /
`matching.signature_fingerprint`): the fingerprint ignores the function's own name and all
cosmetic formatting (quotes, whitespace, optional semicolons, trailing commas, redundant
parens) but changes on a real body/signature edit. The pairwise-distinctness battery below is the
guard against a silent hole in the lossy operator/modifier allowlist (not a proof of completeness).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_typescript")

from riskratchet.typescript import discover_typescript
from riskratchet.typescript_identity import body_fingerprint, signature_fingerprint


def _one(tmp_path: Path, src: str, name: str) -> tuple[str, str]:
    """(fingerprint, signature) of the single function in `src`."""
    path = tmp_path / f"{name}.ts"
    path.write_text(src, encoding="utf-8")
    fns = discover_typescript(path, root=tmp_path)
    assert len(fns) == 1, [fn.id.qualname for fn in fns]
    fn = fns[0]
    assert fn.fingerprint is not None and fn.signature is not None
    return fn.fingerprint, fn.signature


def test_discovery_populates_identity(tmp_path: Path) -> None:
    fp, sig = _one(tmp_path, "export function f(x: number) { return x + 1; }", "a")
    assert len(fp) == 64 and len(sig) == 64  # sha256 hex, like the Python backend
    assert fp != sig


def test_fingerprint_ignores_own_name(tmp_path: Path) -> None:
    foo = _one(tmp_path, "function foo(x: number) { return x + 1; }", "foo")
    bar = _one(tmp_path, "function bar(x: number) { return x + 1; }", "bar")
    assert foo == bar  # both fingerprint and signature ignore the function's own name


def test_fingerprint_is_quote_insensitive(tmp_path: Path) -> None:
    single = _one(tmp_path, "function f() { return 'hello'; }", "s")
    double = _one(tmp_path, 'function f() { return "hello"; }', "d")
    assert single == double


def test_fingerprint_is_whitespace_and_semicolon_insensitive(tmp_path: Path) -> None:
    tight = _one(tmp_path, "function f(x:number){return x+1}", "t")
    loose = _one(tmp_path, "function f( x : number ) {\n    return x + 1 ;\n}", "l")
    assert tight == loose


def test_fingerprint_ignores_redundant_parens(tmp_path: Path) -> None:
    plain = _one(tmp_path, "function f(x: number) { return x + 1; }", "p")
    parens = _one(tmp_path, "function f(x: number) { return (x + 1); }", "q")
    assert plain == parens


def test_trailing_comma_does_not_change_fingerprint(tmp_path: Path) -> None:
    no_comma = _one(tmp_path, "function f(a: number, b: number) { return a + b; }", "n")
    trailing = _one(tmp_path, "function f(a: number, b: number,) { return a + b; }", "c")
    assert no_comma == trailing


def test_body_edit_changes_fingerprint_but_not_signature(tmp_path: Path) -> None:
    fp1, sig1 = _one(tmp_path, "function f(x: number) { return x + 1; }", "b1")
    fp2, sig2 = _one(tmp_path, "function f(x: number) { return x + 2; }", "b2")
    assert fp1 != fp2  # body changed
    assert sig1 == sig2  # signature unchanged


def test_identifier_rename_in_body_changes_fingerprint(tmp_path: Path) -> None:
    fp1, _ = _one(tmp_path, "function f(x: number) { const y = x; return y; }", "i1")
    fp2, _ = _one(tmp_path, "function f(x: number) { const z = x; return z; }", "i2")
    assert fp1 != fp2  # body fingerprint is sensitive to inner identifier renames (like Python)


def test_signature_change_changes_signature(tmp_path: Path) -> None:
    _, sig1 = _one(tmp_path, "function f(x: number) { return 0; }", "g1")
    _, sig2 = _one(tmp_path, "function f(x: string) { return 0; }", "g2")
    assert sig1 != sig2  # param type changed


def test_operator_is_significant(tmp_path: Path) -> None:
    plus = _one(tmp_path, "function f(a: number, b: number) { return a + b; }", "op1")
    minus = _one(tmp_path, "function f(a: number, b: number) { return a - b; }", "op2")
    assert plus != minus  # `+` vs `-` must not collide


def test_async_is_significant(tmp_path: Path) -> None:
    sync = _one(tmp_path, "function f() { return 1; }", "sync")
    asyn = _one(tmp_path, "async function f() { return 1; }", "async")
    assert sync != asyn  # async changes call semantics, like Python's Async/FunctionDef split


def test_default_parameter_changes_signature(tmp_path: Path) -> None:
    _, sig1 = _one(tmp_path, "function f(x: number) { return x; }", "df1")
    _, sig2 = _one(tmp_path, "function f(x: number = 1) { return x; }", "df2")
    assert sig1 != sig2


# Each snippet holds exactly one discovered function and is structurally distinct from every other,
# so all body fingerprints must be pairwise unique. This is the guard against a silent hole in the
# operator/modifier allowlist — if a semantic token were dropped, two rows here would collide.
_DISTINCT_CASES: dict[str, str] = {
    "plain": "function f(){ return 1; }",
    "if": "function f(a){ if (a) { return 1; } return 0; }",
    "while": "function f(a){ while (a) { a--; } return a; }",
    "for": "function f(){ for (let i = 0; i < 3; i++) {} }",
    "for_of": "function f(xs){ for (const x of xs) { g(x); } }",
    "for_in": "function f(o){ for (const k in o) { g(k); } }",
    "switch": "function f(a){ switch (a) { case 1: break; } }",
    "try_catch": "function f(){ try { g(); } catch (e) { h(e); } }",
    "add": "function f(a, b){ return a + b; }",
    "sub": "function f(a, b){ return a - b; }",
    "mul": "function f(a, b){ return a * b; }",
    "strict_eq": "function f(a, b){ return a === b; }",
    "logical_and": "function f(a, b){ return a && b; }",
    "nullish": "function f(a, b){ return a ?? b; }",
    "not": "function f(a){ return !a; }",
    "negate": "function f(a){ return -a; }",
    "update": "function f(a){ a++; return a; }",
    "member": "function f(o){ return o.x; }",
    "optional_chain": "function f(o){ return o?.x; }",
    "call": "function f(){ return g(); }",
    "new": "function f(){ return new G(); }",
    "spread": "function f(a){ return g(...a); }",
    "await": "async function f(p){ return await p; }",
    "typeof": "function f(a){ return typeof a; }",
    "param_string": "function f(a: string){ return a; }",
    "param_number": "function f(a: number){ return a; }",
    "default_param": "function f(a = 1){ return a; }",
    # These four share the body `return 1` — they can differ ONLY by the modifier keyword, so they
    # directly exercise the allowlist.
    "method_plain": "class C { x(){ return 1; } }",
    "method_get": "class C { get x(){ return 1; } }",
    "method_static": "class C { static x(){ return 1; } }",
    "method_generator": "class C { *x(){ return 1; } }",
}


def test_all_structurally_distinct_snippets_have_unique_fingerprints(tmp_path: Path) -> None:
    fingerprints = {label: _one(tmp_path, src, label)[0] for label, src in _DISTINCT_CASES.items()}
    # No two distinct constructs may share a body fingerprint.
    collisions = [
        (a, b) for a in fingerprints for b in fingerprints if a < b and fingerprints[a] == fingerprints[b]
    ]
    assert collisions == [], f"fingerprint collisions: {collisions}"


def test_probed_pairs_stay_distinct(tmp_path: Path) -> None:
    # Lock in the specific distinctions verified by hand during the slice-5 self-critique.
    assert (
        _one(tmp_path, _DISTINCT_CASES["for_of"], "a")[0] != _one(tmp_path, _DISTINCT_CASES["for_in"], "b")[0]
    )
    assert (
        _one(tmp_path, _DISTINCT_CASES["member"], "c")[0]
        != _one(tmp_path, _DISTINCT_CASES["optional_chain"], "d")[0]
    )
    assert (
        _one(tmp_path, _DISTINCT_CASES["method_get"], "e")[0]
        != _one(tmp_path, _DISTINCT_CASES["method_plain"], "f")[0]
    )
    assert (
        _one(tmp_path, _DISTINCT_CASES["method_static"], "g")[0]
        != _one(tmp_path, _DISTINCT_CASES["method_plain"], "h")[0]
    )


def _fp_of(tmp_path: Path, src: str, name: str, qualname: str) -> str:
    """Body fingerprint of the function named `qualname` in `src` (which may hold several)."""
    path = tmp_path / f"{name}.ts"
    path.write_text(src, encoding="utf-8")
    by_name = {fn.id.qualname: fn for fn in discover_typescript(path, root=tmp_path)}
    fn = by_name[qualname]
    assert fn.fingerprint is not None
    return fn.fingerprint


def test_nested_function_modifier_affects_parent_body(tmp_path: Path) -> None:
    # Regression: modifier capture runs at every function-like node, so a nested arrow's `async`
    # changes the *parent's* body fingerprint (the root-only collision found in the self-critique).
    async_parent = _fp_of(tmp_path, "function p(){ const h = async () => 1; return h; }", "n1", "p")
    sync_parent = _fp_of(tmp_path, "function p(){ const h = () => 1; return h; }", "n2", "p")
    assert async_parent != sync_parent


def test_multiply_operator_is_not_treated_as_generator_modifier(tmp_path: Path) -> None:
    # `*` is a generator modifier only on a function node; as the multiply operator it must not
    # inject a spurious `[*]` prefix (else `a * b` could collide with a generator body).
    assert (
        _one(tmp_path, "function f(a, b){ return a * b; }", "m1")[0]
        != _one(tmp_path, "function f(a, b){ return a + b; }", "m2")[0]
    )


def test_callable_directly_on_a_node() -> None:
    import tree_sitter as ts
    import tree_sitter_typescript as tsts

    parser = ts.Parser(ts.Language(tsts.language_typescript()))
    tree = parser.parse(b"function f(a: number) { return a + 1; }")
    fn_node = tree.root_node.children[0]
    assert fn_node.type == "function_declaration"
    assert len(body_fingerprint(fn_node)) == 64
    assert len(signature_fingerprint(fn_node)) == 64
