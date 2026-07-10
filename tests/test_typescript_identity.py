"""Token-stable identity for discovered TypeScript functions (P20 slice 5, since 0.2.15).

tree-sitter lives in the optional `typescript` extra, so this module skips when it is absent.
The contract under test mirrors the Python backend (`analysis.function_fingerprint` /
`matching.signature_fingerprint`): the fingerprint ignores the function's own name and all
cosmetic formatting (quotes, whitespace, optional semicolons, trailing commas, redundant
parens) but changes on a real body/signature edit, so the language-neutral rename matcher can
consume it unchanged at 0.3.0.
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


def test_callable_directly_on_a_node() -> None:
    import tree_sitter as ts
    import tree_sitter_typescript as tsts

    parser = ts.Parser(ts.Language(tsts.language_typescript()))
    tree = parser.parse(b"function f(a: number) { return a + 1; }")
    fn_node = tree.root_node.children[0]
    assert fn_node.type == "function_declaration"
    assert len(body_fingerprint(fn_node)) == 64
    assert len(signature_fingerprint(fn_node)) == 64
