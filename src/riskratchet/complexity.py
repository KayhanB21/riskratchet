"""Cyclomatic complexity for the functions discovered in a `ParsedFile`.

Primary source is `radon.complexity.cc_visit_ast`; functions that radon does
not surface (nested defs, comprehensions in unusual shapes) fall through to a
direct McCabe walk over the AST node we already hold.
"""

from __future__ import annotations

import ast
from typing import Any

from riskratchet.analysis import DiscoveredFunction, ParsedFile
from riskratchet.models import ComplexityStats


def complexity_for_file(parsed: ParsedFile) -> dict[int, ComplexityStats]:
    """Return cyclomatic complexity per function, keyed by start line.

    radon emits top-level functions and class methods directly; nested
    functions inside other functions are rolled into the parent's complexity
    and not addressable separately. For any function that radon did not emit,
    we compute the McCabe count directly from the AST node.
    """
    by_line = _radon_complexity_by_line(parsed.tree)
    out: dict[int, ComplexityStats] = {}
    for fn in parsed.functions:
        cc = by_line.get(fn.span.start_line)
        if cc is None:
            cc = _manual_cyclomatic(fn.node)
        out[fn.span.start_line] = ComplexityStats(cyclomatic=cc)
    return out


def complexity_for_function(fn: DiscoveredFunction) -> ComplexityStats:
    """One-shot complexity for a single function; convenient for `explain`."""
    return ComplexityStats(cyclomatic=_manual_cyclomatic(fn.node))


def _radon_complexity_by_line(tree: ast.Module) -> dict[int, int]:
    from radon.complexity import cc_visit_ast
    from radon.visitors import Class, Function

    by_line: dict[int, int] = {}
    for block in cc_visit_ast(tree):
        if isinstance(block, Function):
            by_line[block.lineno] = int(block.complexity)
        elif isinstance(block, Class):
            for method in block.methods:
                by_line[method.lineno] = int(method.complexity)
    return by_line


_BRANCH_NODES: tuple[type[ast.AST], ...] = (
    ast.If,
    ast.IfExp,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.Assert,
)


def _manual_cyclomatic(node: ast.AST) -> int:
    """McCabe cyclomatic complexity over a function AST.

    Counts branching points: if/elif/ifexp, loops, except handlers, asserts,
    boolean operators, comprehension filters, and match cases. Starts at 1.
    """
    count = 1
    match_case_t: Any = getattr(ast, "match_case", None)
    for child in ast.walk(node):
        if isinstance(child, _BRANCH_NODES):
            count += 1
        elif isinstance(child, ast.BoolOp):
            count += len(child.values) - 1
        elif isinstance(child, ast.comprehension):
            count += 1 + len(child.ifs)
        elif match_case_t is not None and isinstance(child, match_case_t):
            count += 1
    return count
