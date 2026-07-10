"""EXPERIMENTAL: token-stable identity (fingerprints) for discovered TypeScript functions
(P20, slice 5, since 0.2.15).

Discovery (`typescript.py`) tells us *where* each function is and `typescript_complexity.py`
how branchy it is; this module answers *is this the same function after a rename/move*, by
hashing a normalized serialization of the function's tree-sitter subtree. It mirrors the
Python backend's identity contract (`analysis.function_fingerprint` /
`matching.signature_fingerprint`) so the language-neutral rename matcher (`matching._similarity`,
which only ever compares these strings for **equality**) can consume TS fingerprints unchanged
when TypeScript enters the scoring/baseline pipeline at `0.3.0`.

Still informational this release: the fingerprints are emitted in `scan` JSON/SARIF but nothing
scores or gates on them yet.

Two fingerprints, mirroring Python:

- `body_fingerprint`  — the whole function node (signature *and* body), with the function's own
  **name excluded** (mirrors Python `clone.name = ""`). Sensitive to body and inner-identifier
  edits, so a rewrite changes it.
- `signature_fingerprint` — the same, but with the **body block excluded** too (mirrors Python
  `clone.body = []`). Survives body edits; changes when the call shape (params/types/return)
  changes.

**Normalization** — stable across the formatter's cosmetic choices, because the serializer walks
only *named* tree-sitter nodes:

- Anonymous punctuation (`{ } ( ) , ; : . =>`) is never named, so it is dropped → immune to
  brace/spacing style, optional semicolons (ASI), and trailing commas.
- String/template quotes are anonymous while the `string_fragment` content is named, so `'a'` and
  `"a"` serialize identically → quote-insensitive.
- `parenthesized_expression` is unwrapped, so redundant parens don't change the hash (Python's AST
  already drops them).

But three classes of *semantic* tokens are anonymous in the grammar, so they are added back
explicitly (else `a + b` == `a - b`, `async` == sync, `get x()` == `x()`):

- operators on `binary_expression` / `unary_expression` / `update_expression` /
  `augmented_assignment_expression` (the `operator` field text);
- function/method modifier keywords (`async`, `get`, `set`, `static`, `*`, `abstract`, `readonly`).

tree-sitter is imported only by the caller (`typescript.py`); this module is pure over the `Node`
objects it is handed, so a Python-only install never touches it.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # tree-sitter is an optional runtime import; annotations only
    from tree_sitter import Node

# Anonymous `operator`-field tokens that must survive normalization (`+` vs `-`, `++`, `+=`).
_OPERATOR_NODES = frozenset(
    {
        "binary_expression",
        "unary_expression",
        "update_expression",
        "augmented_assignment_expression",
    }
)
# Anonymous direct-child modifier keywords that materially change a function's identity.
_MODIFIER_TOKENS = frozenset({"async", "get", "set", "static", "*", "abstract", "readonly"})


def body_fingerprint(node: Node) -> str:
    """Stable hash of a function node — signature and body — ignoring its own name and layout."""
    return _hash(_serialize_root(node, include_body=True))


def signature_fingerprint(node: Node) -> str:
    """Stable hash of a function node's signature only — params, type params, return type —
    ignoring its own name, its body, and layout."""
    return _hash(_serialize_root(node, include_body=False))


def _hash(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _serialize_root(node: Node, *, include_body: bool) -> str:
    """Serialize the function node, skipping its `name` field (always) and `body` field (unless
    `include_body`). Only the root carries the skip set — name/body are direct fields here."""
    skip_ids: set[int] = set()
    name_child = node.child_by_field_name("name")
    if name_child is not None:
        skip_ids.add(name_child.id)
    if not include_body:
        body_child = node.child_by_field_name("body")
        if body_child is not None:
            skip_ids.add(body_child.id)
    prefix = _modifier_prefix(node) + _operator_suffix(node)
    inner = "".join(_serialize(child) for child in _named_children(node) if child.id not in skip_ids)
    return f"({node.type}{prefix}{inner})"


def _serialize(node: Node) -> str:
    if node.type == "parenthesized_expression":
        inner = next((c for c in node.children if c.is_named), None)
        if inner is not None:
            return _serialize(inner)
    named = _named_children(node)
    if not named:
        return f"({node.type} {_leaf_text(node)})"
    body = "".join(_serialize(child) for child in named)
    return f"({node.type}{_operator_suffix(node)}{body})"


def _named_children(node: Node) -> list[Node]:
    return [child for child in node.children if child.is_named]


def _operator_suffix(node: Node) -> str:
    if node.type not in _OPERATOR_NODES:
        return ""
    operator = node.child_by_field_name("operator")
    return f":{_leaf_text(operator)}" if operator is not None else ""


def _modifier_prefix(node: Node) -> str:
    mods = sorted(
        child.type for child in node.children if not child.is_named and child.type in _MODIFIER_TOKENS
    )
    return "".join(f"[{mod}]" for mod in mods)


def _leaf_text(node: Node) -> str:
    return node.text.decode("utf-8", "replace") if node.text is not None else ""
