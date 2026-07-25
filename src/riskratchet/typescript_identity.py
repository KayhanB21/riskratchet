"""Token-stable identity (fingerprints) for discovered TypeScript functions (P20, since 0.2.15;
consumed by rename matching since 0.3.0).

Discovery (`typescript.py`) tells us *where* each function is and `typescript_complexity.py`
how branchy it is; this module answers *is this the same function after a rename/move*, by
hashing a normalized serialization of the function's tree-sitter subtree. It is **analogous to**
the Python backend's identity contract (`analysis.function_fingerprint` /
`matching.signature_fingerprint`) — same two-fingerprint split, same SHA-256 `str` shape — so it
slots into the language-neutral rename matcher (`matching._similarity`, which only ever compares
these strings for **equality**), which since `0.3.0` matches persisted TS baseline entries. It is
**not** a faithful port of `ast.dump`: it is a lossy, hand-curated projection (walk the named
nodes, add back a small operator/keyword allowlist). `tests/test_typescript_identity.py` carries a
pairwise-distinctness battery as the guard; the `0.3.0` fingerprint-completeness audit (B7) probed
the anonymous-keyword classes the allowlist could miss and closed the two it found (`const`/`let`
and `readonly` parameter properties) in scheme 2 — see `SCHEME_VERSION`. Residual known collapse:
`const x = 1` vs `let x = 1` and a lone `readonly` are now distinct, but the projection is still
lossy by construction, so completeness is characterized, not proven.

**Durability — two things this hash depends on, and the 0.3.0 requirement.** The payload embeds
`SCHEME_VERSION` (bump on any serializer change). It does **not** embed the tree-sitter-typescript
**grammar version**, which it also depends on — the serialization hashes grammar node-type strings,
so a grammar upgrade (e.g. a dependabot bump) can silently change every fingerprint. That is
harmless while nothing consumes them, but before `0.3.0` persists TS fingerprints in a baseline the
baseline **must** record the grammar + `SCHEME_VERSION`, and the grammar must be pinned or
version-gated (see `docs/language-backend-contract.md §5`). Both durability inputs are now
retrievable at runtime: `SCHEME_VERSION` (below) and `grammar_version()` (reads the installed
grammar's distribution version); the `0.3.0` baseline records both and suppresses TS rename-matching
when a persisted value ≠ runtime, so a bump is detected rather than read as a mass rename (B6). The
grammar stays tightly minor-bounded in the `[typescript]` extra; the recorded version is the gate.
Known limitation, now guarded: semantic-keyword
capture applies at every matching node (root and nested), so a parent body reflects a nested
function's `async`/generator (or a nested `const`/`readonly`) — the earlier root-only collision is
fixed.

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

But several classes of *semantic* tokens are anonymous in the grammar, so they are added back
explicitly (else `a + b` == `a - b`, `async` == sync, `get x()` == `x()`, `const` == `let`):

- operators on `binary_expression` / `unary_expression` / `update_expression` /
  `augmented_assignment_expression` (the `operator` field text);
- the per-node-type keyword allowlist `_SEMANTIC_KEYWORDS`: function/method modifiers (`async`,
  `get`, `set`, `static`, `*`), the `lexical_declaration` kind (`const` vs `let`), and a `readonly`
  parameter property. Each token is scoped to its owning node type, so the generator `*` never
  collides with the multiply operator.

tree-sitter is imported only by the caller (`typescript.py`); this module is pure over the `Node`
objects it is handed, so a Python-only install never touches it.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # tree-sitter is an optional runtime import; annotations only
    from tree_sitter import Node

SCHEME_VERSION = 2
"""Serialization-scheme version, embedded in every hash so a fingerprint self-identifies its scheme.

Bump whenever the serializer below changes shape, so fingerprints from different schemes never
silently collide or falsely diverge. **This does not capture the tree-sitter-typescript grammar
version**, which the fingerprints also depend on (they hash grammar node-type strings) — call
`grammar_version()` for that second durability input; see the module docstring and
`docs/language-backend-contract.md §5` for the 0.3.0 durability requirement.

Scheme history:
- 1 (0.2.15): initial token-stable serialization (operators + function modifiers).
- 2 (0.3.0): the fingerprint-completeness audit closed two anonymous-keyword collisions before the
  first release that persists TS fingerprints in a baseline — `const` vs `let` on a
  `lexical_declaration`, and a `readonly` parameter property. Bumped now (rather than post-release)
  precisely because no shipped baseline is pinned to scheme 1, so there is no re-baseline cost.
"""

# PyPI distribution name of the grammar (import name is `tree_sitter_typescript`). The fingerprints
# hash this grammar's node-type strings, so its version is a durability input alongside SCHEME_VERSION.
GRAMMAR_DISTRIBUTION = "tree-sitter-typescript"


def grammar_version() -> str:
    """Return the installed tree-sitter-typescript distribution version (e.g. ``"0.23.2"``).

    The fingerprints hash grammar node-type strings, so the grammar version is a durability input on
    par with `SCHEME_VERSION`: a grammar upgrade can silently change every fingerprint. The 0.3.0
    baseline records both `SCHEME_VERSION` and this value in its top-level ``identity`` block (B6) so
    a later grammar/scheme mismatch is *detected* — and TS rename-matching suppressed for that run —
    rather than read as a mass rename. This reads distribution metadata only; it does not import
    tree-sitter, so a Python-only caller could technically call it, but it raises
    `importlib.metadata.PackageNotFoundError` when the ``[typescript]`` extra is absent. Callers
    invoke it only while TypeScript analysis is active (a TS entry is being discovered or persisted).
    """
    return importlib.metadata.version(GRAMMAR_DISTRIBUTION)


# Anonymous `operator`-field tokens that must survive normalization (`+` vs `-`, `++`, `+=`).
_OPERATOR_NODES = frozenset(
    {
        "binary_expression",
        "unary_expression",
        "update_expression",
        "augmented_assignment_expression",
    }
)
# Anonymous keyword tokens that materially change identity, keyed by the node type they hang under.
# Scoping each token to its owning node type keeps them from colliding with same-spelled tokens
# elsewhere — the generator `*` is read only under function-like nodes, never as the multiply
# operator on a `binary_expression` (handled by `_operator_suffix` instead). Each entry was probed
# against the live grammar (see the fingerprint-completeness audit / `tests/test_typescript_identity.py`):
#   - function-like modifiers: `async`, `get`/`set` accessors, `static`, `*` generators.
#   - `lexical_declaration` kind: `const` vs `let` (a `var` is a distinct `variable_declaration`
#     node, so it never reaches here) — a real reassignability difference that would otherwise
#     collide, closed in scheme 2.
#   - parameter `readonly`: a `readonly` parameter property declares a class field; without it the
#     two constructors collide. `public`/`private`/`protected` need no entry — they parse as a
#     *named* `accessibility_modifier` node, already serialized. Closed in scheme 2.
# `abstract` is intentionally absent — abstract method signatures have no body and are not discovered.
_FUNCTION_MODIFIERS = frozenset({"async", "get", "set", "static", "*"})
_SEMANTIC_KEYWORDS: dict[str, frozenset[str]] = {
    "function_declaration": _FUNCTION_MODIFIERS,
    "function_expression": _FUNCTION_MODIFIERS,
    "arrow_function": _FUNCTION_MODIFIERS,
    "method_definition": _FUNCTION_MODIFIERS,
    "lexical_declaration": frozenset({"const", "let"}),
    "required_parameter": frozenset({"readonly"}),
    "optional_parameter": frozenset({"readonly"}),
}


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
    # Semantic anonymous keywords are read at every matching node — root AND nested — so a parent's
    # body fingerprint reflects a nested function's `async`/`*` or a nested `const`/`readonly`.
    prefix = _keyword_prefix(node) + _operator_suffix(node)
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


def _keyword_prefix(node: Node) -> str:
    allowed = _SEMANTIC_KEYWORDS.get(node.type)
    if not allowed:
        return ""
    toks = sorted(child.type for child in node.children if not child.is_named and child.type in allowed)
    return "".join(f"[{tok}]" for tok in toks)


def _leaf_text(node: Node) -> str:
    return node.text.decode("utf-8", "replace") if node.text is not None else ""
