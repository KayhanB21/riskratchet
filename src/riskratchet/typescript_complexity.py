"""EXPERIMENTAL: McCabe cyclomatic complexity for discovered TypeScript functions
(P20, slice 4, since 0.2.14).

Discovery (`typescript.py`) tells us *where* each TypeScript function is; this module
answers *how branchy* it is, by counting decision points over the function's tree-sitter
subtree. The result is a `ComplexityStats` — the same shape the Python backend produces in
`complexity.py` — so the two backends stay comparable.

**Parity with the Python algorithm.** The reference is `complexity.py::_manual_cyclomatic`:
start at **1** and add one per branching point. The Python↔TS node mapping is:

    if / elif        -> if_statement            (each `else if` is a nested if_statement)
    ternary (a?b:c)  -> ternary_expression
    for / for-of/in  -> for_statement / for_in_statement
    while            -> while_statement
    (no Python peer) -> do_statement            (do/while — still one decision point)
    except           -> catch_clause
    match case       -> switch_case             (NOT switch_default — the fall-through)
    and / or         -> binary_expression `&&` / `||`   (one per operator; chains nest,
                        so `a && b && c` -> +2, matching Python's `len(values) - 1`)

**Two deliberate TS-specific judgment calls, documented in
`docs/language-backend-contract.md §3`:**

- **`??` (nullish coalescing) IS counted** — it short-circuits exactly like `&&`/`||`, so it
  is a genuine branch. Python has no equivalent operator.
- **Optional chaining `?.` is NOT counted.** It has no McCabe/Python counterpart, and it is
  so common in idiomatic TS that counting every `?.` would systematically inflate TS
  complexity relative to Python and break the cross-backend comparability this parity exists
  to preserve.

Nested functions are pruned: the walk stops descending at a nested function boundary, so each
function's complexity is its own (mirroring radon's per-block behaviour, the Python backend's
preferred path). Every nested function is discovered separately and gets its own count.

Informational only — like discovery, it never feeds scoring, baseline, or gating in 0.2.x.
tree-sitter is imported only by the caller (`typescript.py`); this module is pure over the
`Node` objects it is handed, so a Python-only install never touches it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # tree-sitter is an optional runtime import; annotations only
    from tree_sitter import Node

# Branch nodes that each add +1, the TS analog of `complexity.py::_BRANCH_NODES`.
# `switch_default` is deliberately absent (the catch-all is not a new decision point).
_DECISION_KINDS = frozenset(
    {
        "if_statement",
        "ternary_expression",
        "for_statement",
        "for_in_statement",  # covers both `for…of` and `for…in`
        "while_statement",
        "do_statement",
        "catch_clause",
        "switch_case",
    }
)
# Short-circuiting logical operators inside `binary_expression`. `??` is included by design
# (it short-circuits like `&&`/`||`); optional chaining `?.` is intentionally excluded.
_LOGICAL_OPERATORS = frozenset({"&&", "||", "??"})
# A nested function is its own unit — do not descend into or count it here.
_NESTED_FUNCTION_KINDS = frozenset(
    {"function_declaration", "function_expression", "arrow_function", "method_definition"}
)


def cyclomatic_for_node(node: Node) -> int:
    """McCabe cyclomatic complexity of a single TypeScript function node.

    Starts at 1 and adds one per decision point in the function's own body, pruning nested
    function subtrees (each is discovered and scored on its own). Mirrors the Python
    `_manual_cyclomatic` walk over the tree-sitter grammar; see the module docstring for the
    exact node mapping and the `??`-in / `?.`-out decisions.
    """
    count = 1
    # Seed with the function's children, not the node itself, so the outer function is not
    # treated as a nested boundary that stops the walk before it starts.
    stack: list[Node] = list(node.children)
    while stack:
        current = stack.pop()
        if current.type in _NESTED_FUNCTION_KINDS:
            continue  # own unit — neither counted nor descended into
        count += _decision_increment(current)
        stack.extend(current.children)
    return count


def _decision_increment(node: Node) -> int:
    t = node.type
    if t in _DECISION_KINDS:
        return 1
    if t == "binary_expression":
        operator = node.child_by_field_name("operator")
        if operator is not None and operator.type in _LOGICAL_OPERATORS:
            return 1
    return 0
