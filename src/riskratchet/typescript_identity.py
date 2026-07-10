"""EXPERIMENTAL: token-stable identity (fingerprints) for discovered TypeScript functions
(P20, slice 5, since 0.2.15).

Discovery (`typescript.py`) tells us *where* each function is and `typescript_complexity.py`
how branchy it is; this module answers *is this the same function after a rename/move*, by
hashing a normalized serialization of the function's tree-sitter subtree. It is **analogous to**
the Python backend's identity contract (`analysis.function_fingerprint` /
`matching.signature_fingerprint`) — same two-fingerprint split, same SHA-256 `str` shape — so it is
*intended* to slot into the language-neutral rename matcher (`matching._similarity`, which only ever
compares these strings for **equality**) when TypeScript enters the scoring/baseline pipeline at
`0.3.0`. It is **not** a faithful port of `ast.dump`: it is a lossy, hand-curated projection (walk
the named nodes, add back a small operator/modifier allowlist), so its completeness is not proven —
`tests/test_typescript_identity.py` carries a pairwise-distinctness battery as the guard.

Still informational this release: the fingerprints are emitted in `scan` JSON/SARIF but nothing
scores or gates on them yet.

**Durability — two things this hash depends on, and the 0.3.0 requirement.** The payload embeds
`SCHEME_VERSION` (bump on any serializer change). It does **not** embed the tree-sitter-typescript
**grammar version**, which it also depends on — the serialization hashes grammar node-type strings,
so a grammar upgrade (e.g. a dependabot bump) can silently change every fingerprint. That is
harmless while nothing consumes them, but before `0.3.0` persists TS fingerprints in a baseline the
baseline **must** record the grammar + `SCHEME_VERSION`, and the grammar must be pinned or
version-gated (see `docs/language-backend-contract.md §5`). Known limitation, now guarded: modifier
capture applies at every function-like node (root and nested), so a parent body reflects a nested
function's `async`/generator — the earlier root-only collision is fixed.

Two fingerprints, analogous to Python:

- `body_fingerprint`  — the whole function node (signature *and* body), with the function's own
  **name excluded** (like Python `clone.name = ""`). Sensitive to body and inner-identifier
  edits, so a rewrite changes it.
- `signature_fingerprint` — the same, but with the **body block excluded** too (like Python
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
- function/method modifier keywords (`async`, `get`, `set`, `static`, `*`) — read only on
  function-like nodes, so the generator `*` never collides with the multiply operator.

tree-sitter is imported only by the caller (`typescript.py`); this module is pure over the `Node`
objects it is handed, so a Python-only install never touches it.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # tree-sitter is an optional runtime import; annotations only
    from tree_sitter import Node

SCHEME_VERSION = 1
"""Serialization-scheme version, embedded in every hash so a fingerprint self-identifies its scheme.

Bump whenever the serializer below changes shape, so fingerprints from different schemes never
silently collide or falsely diverge. **This does not capture the tree-sitter-typescript grammar
version**, which the fingerprints also depend on (they hash grammar node-type strings) — see the
module docstring and `docs/language-backend-contract.md §5` for the 0.3.0 durability requirement.
"""

# Anonymous `operator`-field tokens that must survive normalization (`+` vs `-`, `++`, `+=`).
_OPERATOR_NODES = frozenset(
    {
        "binary_expression",
        "unary_expression",
        "update_expression",
        "augmented_assignment_expression",
    }
)
# Function-like nodes: the only nodes whose anonymous modifier keywords are read (see below). Scoping
# `_modifier_prefix` to these avoids a false positive — `*` is a generator modifier *here* but the
# multiply operator on a `binary_expression` (handled by `_operator_suffix` instead).
_FUNCTION_LIKE = frozenset(
    {"function_declaration", "function_expression", "arrow_function", "method_definition"}
)
# Anonymous direct-child modifier keywords that materially change a function's identity. Each is a
# verified direct child of a *discovered* function/method node: `async`, `get`/`set` accessors,
# `static` methods, and `*` generators (all probed). `abstract`/`readonly` are intentionally absent
# — abstract signatures are not discovered, and `readonly` sits on a parameter, not the method node.
_MODIFIER_TOKENS = frozenset({"async", "get", "set", "static", "*"})


def body_fingerprint(node: Node) -> str:
    """Stable hash of a function node — signature and body — ignoring its own name and layout."""
    return _hash(_serialize_function(node, include_body=True))


def signature_fingerprint(node: Node) -> str:
    """Stable hash of a function node's signature only — params, type params, return type —
    ignoring its own name, its body, and layout."""
    return _hash(_serialize_function(node, include_body=False))


def _hash(payload: str) -> str:
    return hashlib.sha256(f"v{SCHEME_VERSION}:{payload}".encode()).hexdigest()


def _serialize_function(node: Node, *, include_body: bool) -> str:
    """Serialize a function node, skipping its own `name` (always) and `body` (unless `include_body`).
    Only these root-level fields are skipped; `skip_ids` holds their unique node ids, so threading it
    through the recursion is a no-op below the root (no other node shares those ids)."""
    skip_ids: set[int] = set()
    name_child = node.child_by_field_name("name")
    if name_child is not None:
        skip_ids.add(name_child.id)
    if not include_body:
        body_child = node.child_by_field_name("body")
        if body_child is not None:
            skip_ids.add(body_child.id)
    return _serialize(node, frozenset(skip_ids))


def _serialize(node: Node, skip_ids: frozenset[int] = frozenset()) -> str:
    if node.type == "parenthesized_expression":
        inner = next((c for c in node.children if c.is_named and c.id not in skip_ids), None)
        if inner is not None:
            return _serialize(inner, skip_ids)
    prefix = _operator_suffix(node)
    # Modifier keywords are read at every function-like node — root AND nested — so a parent's body
    # fingerprint reflects a nested function's `async`/`get`/`set`/`static`/`*`, not only the root's.
    if node.type in _FUNCTION_LIKE:
        prefix = _modifier_prefix(node) + prefix
    named = [child for child in node.children if child.is_named and child.id not in skip_ids]
    if not named:
        return f"({node.type}{prefix} {_leaf_text(node)})"
    body = "".join(_serialize(child, skip_ids) for child in named)
    return f"({node.type}{prefix}{body})"


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
