"""EXPERIMENTAL: McCabe cyclomatic complexity for discovered TypeScript functions
(P20, slice 4, since 0.2.14).

Discovery (`typescript.py`) tells us *where* each TypeScript function is; this module
answers *how branchy* it is, by counting decision points over the function's tree-sitter
subtree, yielding a `ComplexityStats`.

**Reference: ESLint's `complexity` rule** — the metric TS developers already know. Start at
**1** and add one per decision point, **per function**. The rule's increment set, mapped to
tree-sitter nodes:

    if / else if          -> if_statement            (each `else if` is a nested if_statement)
    ternary (a?b:c)       -> ternary_expression
    for / for-of / for-in -> for_statement / for_in_statement
    while / do-while      -> while_statement / do_statement
    catch                 -> catch_clause
    case (not default)    -> switch_case             (switch_default is not a decision point)
    && / || / ??          -> binary_expression with a `&&`/`||`/`??` operator (one per
                             operator; chains nest, so `a && b && c` -> +2)
    default parameter     -> required/optional_parameter with a default value, and each
                             destructuring default (object_assignment_pattern /
                             assignment_pattern) — ESLint's `AssignmentPattern`

- **`??` IS counted** (ESLint counts it — it is a `LogicalExpression`). **Optional chaining
  `?.` is NOT** (ESLint does not count it either; it also has no Python `_manual_cyclomatic`
  counterpart, and counting every `?.` would badly inflate idiomatic TS).
- **Nested functions are pruned** — the walk stops at a nested function boundary, so each
  function's complexity is its own, exactly as ESLint scores each function separately.

**Two intentional divergences from riskratchet's Python `complexity.py::_manual_cyclomatic`**
(documented in `docs/language-backend-contract.md §3`): the Python fallback (a) does **not**
count default parameters, and (b) does **not** prune nested functions (`ast.walk` descends into
them). So TS (ESLint-aligned) and Python complexity are **not directly comparable** for those
two shapes — a reconciliation item for slice 5, when both backends feed one scoring pipeline.
Until then this is informational only: it never feeds scoring, baseline, or gating.

tree-sitter is imported only by the caller (`typescript.py`); this module is pure over the
`Node` objects it is handed, so a Python-only install never touches it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # tree-sitter is an optional runtime import; annotations only
    from tree_sitter import Node

# Branch nodes that each add +1 (ESLint's control-flow increment set).
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
        # Destructuring defaults, in a parameter or the body — ESLint's `AssignmentPattern`.
        "object_assignment_pattern",  # `{ a = 1 }`
        "assignment_pattern",  # `[ b = 1 ]`
    }
)
# Short-circuiting logical operators inside `binary_expression`. `??` is included (ESLint counts
# it — it is a `LogicalExpression`); optional chaining `?.` is intentionally excluded (so does
# ESLint).
_LOGICAL_OPERATORS = frozenset({"&&", "||", "??"})
# Parameter nodes that count only when they carry a default value (`x = 1`, `x: T = 1`) — the
# other half of ESLint's `AssignmentPattern`. A plain `x` / `x?: T` has no `value` field.
_PARAMETER_KINDS = frozenset({"required_parameter", "optional_parameter"})
# A nested function is its own unit — do not descend into or count it here.
_NESTED_FUNCTION_KINDS = frozenset(
    {"function_declaration", "function_expression", "arrow_function", "method_definition"}
)


def cyclomatic_for_node(node: Node) -> int:
    """McCabe cyclomatic complexity of a single TypeScript function node.

    Starts at 1 and adds one per decision point in the function's own body, pruning nested
    function subtrees (each is discovered and scored on its own). Matches ESLint's `complexity`
    rule over the tree-sitter grammar; see the module docstring for the exact node set, the
    `??`-in / `?.`-out decisions, and the divergences from the Python backend.
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
    if t in _PARAMETER_KINDS:
        return 1 if node.child_by_field_name("value") is not None else 0
    if t == "binary_expression":
        operator = node.child_by_field_name("operator")
        if operator is not None and operator.type in _LOGICAL_OPERATORS:
            return 1
    return 0
