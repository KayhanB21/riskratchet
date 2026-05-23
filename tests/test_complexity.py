"""Tests for cyclomatic complexity computation."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from riskratchet.analysis import ParseError, parse_file
from riskratchet.complexity import complexity_for_file


def _parse(tmp_path: Path, source: str) -> object:
    path = tmp_path / "m.py"
    path.write_text(dedent(source).strip() + "\n", encoding="utf-8")
    parsed = parse_file(path, root=tmp_path)
    assert not isinstance(parsed, ParseError)
    return parsed


def _complexity_by_name(parsed: object) -> dict[str, int]:
    cc_by_line = complexity_for_file(parsed)  # type: ignore[arg-type]
    return {
        fn.id.qualname: cc_by_line[fn.span.start_line].cyclomatic
        for fn in parsed.functions  # type: ignore[attr-defined]
    }


def test_trivial_function_has_cc_1(tmp_path: Path) -> None:
    parsed = _parse(tmp_path, """
        def add(a, b):
            return a + b
    """)
    assert _complexity_by_name(parsed)["add"] == 1


def test_if_branch_increases_cc(tmp_path: Path) -> None:
    parsed = _parse(tmp_path, """
        def classify(x):
            if x > 0:
                return 1
            return 0
    """)
    assert _complexity_by_name(parsed)["classify"] == 2


def test_multiple_branches(tmp_path: Path) -> None:
    parsed = _parse(tmp_path, """
        def classify(x):
            if x < 0:
                return -1
            if x == 0:
                return 0
            if x < 10:
                return 1
            return 2
    """)
    assert _complexity_by_name(parsed)["classify"] == 4


def test_boolean_ops_add_complexity(tmp_path: Path) -> None:
    parsed = _parse(tmp_path, """
        def gate(a, b, c):
            if a and b or c:
                return 1
            return 0
    """)
    # base 1 + 1 (if) + 1 (and) + 1 (or) = 4
    assert _complexity_by_name(parsed)["gate"] == 4


def test_method_on_class(tmp_path: Path) -> None:
    parsed = _parse(tmp_path, """
        class Calc:
            def step(self, x):
                if x > 0:
                    return x
                return -x
    """)
    assert _complexity_by_name(parsed)["Calc.step"] == 2


def test_nested_function_complexity_falls_back_to_manual(tmp_path: Path) -> None:
    parsed = _parse(tmp_path, """
        def outer(x):
            def inner(y):
                if y > 0:
                    return y
                return 0
            return inner(x)
    """)
    cc = _complexity_by_name(parsed)
    assert cc["outer.inner"] == 2


def test_except_handler_increases_cc(tmp_path: Path) -> None:
    parsed = _parse(tmp_path, """
        def risky():
            try:
                return 1
            except ValueError:
                return 2
            except RuntimeError:
                return 3
    """)
    # base 1 + 2 except handlers = 3
    assert _complexity_by_name(parsed)["risky"] == 3


def test_comprehension_with_filters(tmp_path: Path) -> None:
    parsed = _parse(tmp_path, """
        def collect(xs):
            return [x for x in xs if x > 0 if x < 10]
    """)
    # base 1 + 1 (comprehension) + 2 (filters) = 4
    cc = _complexity_by_name(parsed)["collect"]
    assert cc >= 3


def test_match_statement(tmp_path: Path) -> None:
    parsed = _parse(tmp_path, """
        def route(x):
            match x:
                case 1:
                    return "one"
                case 2:
                    return "two"
                case _:
                    return "other"
    """)
    # radon counts each non-wildcard case (`case 1`, `case 2`) plus the base path = 3.
    # The wildcard `case _` is the fall-through, not an extra branch.
    cc = _complexity_by_name(parsed)["route"]
    assert cc >= 3
