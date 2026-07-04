"""Cyclomatic complexity for discovered TypeScript functions (P20 slice 4, since 0.2.14).

tree-sitter lives in the optional `typescript` extra, so this module skips when it is absent.
The counts match ESLint's `complexity` rule over the TS grammar; the decisions under test are
that `??` IS counted, optional chaining `?.` is NOT, default parameters (`AssignmentPattern`)
ARE, `switch` `default` is not, and nested functions are pruned (each is its own unit).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_typescript")

from riskratchet.typescript import discover_typescript
from riskratchet.typescript_complexity import cyclomatic_for_node

FIXTURES = Path(__file__).parent / "fixtures" / "typescript"


def _cc(tmp_path: Path, src: str) -> dict[str, int]:
    """qualname -> cyclomatic for a TS snippet written to a temp file."""
    path = tmp_path / "snippet.ts"
    path.write_text(src, encoding="utf-8")
    return _cc_map(discover_typescript(path, root=tmp_path))


def _cc_map(functions: list) -> dict[str, int]:  # type: ignore[type-arg]
    out: dict[str, int] = {}
    for fn in functions:
        assert fn.complexity is not None  # discovery always computes it
        out[fn.id.qualname] = fn.complexity.cyclomatic
    return out


def test_straight_line_is_one(tmp_path: Path) -> None:
    assert _cc(tmp_path, "export function f(x: number) { return x + 1; }") == {"f": 1}


def test_if_and_else_if_each_count(tmp_path: Path) -> None:
    src = "export function f(a: number) { if (a > 0) {} else if (a < 0) {} else {} }"
    assert _cc(tmp_path, src) == {"f": 3}  # base 1 + if + else-if (a nested if_statement)


def test_ternary_counts(tmp_path: Path) -> None:
    assert _cc(tmp_path, "export function f(a: number) { return a > 0 ? 1 : 2; }") == {"f": 2}


def test_loops_each_count(tmp_path: Path) -> None:
    for_of = _cc(tmp_path, "export function f(xs: number[]) { for (const x of xs) {} }")
    assert for_of == {"f": 2}
    for_in = _cc(tmp_path, "export function g(o: object) { for (const k in o) {} }")
    assert for_in == {"g": 2}
    while_do = _cc(tmp_path, "export function h(n: number) { while (n) {} do {} while (n); }")
    assert while_do == {"h": 3}  # base + while + do


def test_switch_counts_cases_not_default(tmp_path: Path) -> None:
    src = "export function f(n: number) { switch (n) { case 1: break; case 2: break; default: } }"
    assert _cc(tmp_path, src) == {"f": 3}  # base + two cases; default is not a decision point


def test_catch_counts(tmp_path: Path) -> None:
    assert _cc(tmp_path, "export function f() { try {} catch (e) {} }") == {"f": 2}


def test_logical_operators_count_each(tmp_path: Path) -> None:
    # `a && b && c` -> +2 (one per operator, like Python's len(values) - 1), plus `||`.
    src = "export function f(a: any, b: any, c: any) { return (a && b && c) || a; }"
    assert _cc(tmp_path, src) == {"f": 4}


def test_nullish_coalescing_is_counted(tmp_path: Path) -> None:
    assert _cc(tmp_path, "export function f(a: any) { return a ?? 0; }") == {"f": 2}


def test_optional_chaining_is_not_counted(tmp_path: Path) -> None:
    # `?.` short-circuits but ESLint's complexity rule does not count it, so neither do we.
    src = "export function f(o: { a?: { b?: number } }) { return o?.a?.b; }"
    assert _cc(tmp_path, src) == {"f": 1}


def test_default_parameters_count(tmp_path: Path) -> None:
    # ESLint's `AssignmentPattern`: a simple param default and a typed param default each +1;
    # a param without a default does not count.
    assert _cc(tmp_path, "export function f(x = 1, y: number = 2, z) { return z; }") == {"f": 3}


def test_destructuring_defaults_count_in_params_and_body(tmp_path: Path) -> None:
    # Object/array destructuring defaults count whether in a parameter or the body.
    params = "export function f({ a = 1 } = {}, [b = 2] = []) { return a + b; }"
    assert _cc(tmp_path, params) == {"f": 5}  # base + two param `= …` defaults + `a = 1` + `b = 2`
    body = "export function g(o: any) { const { p = 1 } = o; return p; }"
    assert _cc(tmp_path, body) == {"g": 2}


def test_nested_function_is_pruned_from_parent(tmp_path: Path) -> None:
    # The parent counts only its own branch; the inner arrow's ternary belongs to `inner`.
    src = "export function f(n: number) { const inner = (x: number) => (x > 0 ? 1 : 2); return inner(n); }"
    assert _cc(tmp_path, src) == {"f": 1, "f.inner": 2}


def test_fixture_complexity_values() -> None:
    fns = _cc_map(discover_typescript(FIXTURES / "complexity_cases.ts", root=FIXTURES))
    assert fns == {
        "straight": 1,
        "branchy": 7,
        "loopy": 8,
        "optionalChainAndNested": 2,
        "optionalChainAndNested.inner": 2,
        "withDefaults": 6,
    }


def test_cyclomatic_for_node_is_callable_directly() -> None:
    # Direct unit call on a parsed node (not only through discovery).
    import tree_sitter as ts
    import tree_sitter_typescript as tsts

    parser = ts.Parser(ts.Language(tsts.language_typescript()))
    tree = parser.parse(b"function f(a: number) { return a > 0 && a < 10 ? 1 : 2; }")
    fn_node = tree.root_node.children[0]
    assert fn_node.type == "function_declaration"
    assert cyclomatic_for_node(fn_node) == 3  # base + && + ternary
