"""EXPERIMENTAL TypeScript function discovery (P20, slice 2, since 0.2.12).

Discovery only — no scoring, no coverage, no baseline, no gating. The output is
informational and its shape may change. The Python analyzer is unaffected: this module
is never imported by `engine.analyze`; it is reached solely through the
`scan --experimental-typescript` path.

Parsing uses tree-sitter, pulled in by the optional `typescript` extra
(`pip install 'riskratchet[typescript]'`). A default Python-only install never imports
it. The tree-sitter node taxonomy this relies on was confirmed by a spike against
`tests/fixtures/typescript/` (see `docs/typescript-parser-decision.md`).

Discovered: top-level function declarations, class methods, and named (const/let-assigned)
arrow and function expressions, with React function components falling out naturally as
exported functions/arrows. Deliberately skipped: anonymous inline callbacks (e.g.
`xs.map(x => …)`), object-literal methods, interface/abstract method *signatures* (no
body), and generated files (`@generated` header or `*.pb.ts` / `*.gen.ts` name).
Not yet supported (silently skipped): generator functions and async iterators.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .analysis import _any_match, _has_hidden_parent, _relative_posix
from .models import FunctionId, FunctionSpan

if TYPE_CHECKING:  # annotations only; tree-sitter is an optional runtime import
    from tree_sitter import Node

_INSTALL_HINT = "TypeScript discovery needs the optional extra: pip install 'riskratchet[typescript]'"
_TS_SUFFIXES = (".ts", ".tsx")
_GENERATED_NAME_RE = re.compile(r"\.(pb|gen)\.tsx?$")
_GENERATED_HEADER_MARKER = "@generated"
_FUNCTION_KINDS = frozenset({"function_declaration", "function_expression", "arrow_function"})

_LANGUAGES: dict[str, Any] = {}


def _require_tree_sitter() -> tuple[Any, Any]:
    try:
        import tree_sitter
        import tree_sitter_typescript
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImportError(_INSTALL_HINT) from exc
    return tree_sitter, tree_sitter_typescript


@dataclass(frozen=True, slots=True)
class TsFunction:
    """A function discovered in a TypeScript source file. Language-neutral shape, reusing
    `FunctionId`/`FunctionSpan`; no fingerprint/score (discovery is informational)."""

    id: FunctionId
    span: FunctionSpan
    is_public: bool
    is_async: bool
    kind: str  # "function" | "method" | "arrow"


def iter_typescript_files(
    paths: list[Path],
    *,
    root: Path,
    exclude: list[str] | None = None,
    include: list[str] | None = None,
) -> list[Path]:
    """Walk `paths` and return matching `.ts`/`.tsx` files (mirrors `iter_python_files`)."""
    exclude = exclude or []
    include = include or []
    seen: set[Path] = set()
    out: list[Path] = []
    for entry in paths:
        for path in _walk_typescript(entry):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            relative = _relative_posix(path, root)
            if include and not _any_match(relative, include):
                continue
            if exclude and _any_match(relative, exclude):
                continue
            out.append(path)
    out.sort()
    return out


def _walk_typescript(entry: Path) -> list[Path]:
    if entry.is_file():
        return [entry] if entry.suffix in _TS_SUFFIXES else []
    if not entry.is_dir():
        return []
    found: list[Path] = []
    for suffix in _TS_SUFFIXES:
        found.extend(p for p in entry.rglob(f"*{suffix}") if not _has_hidden_parent(p, entry))
    return found


def _language(suffix: str) -> Any:
    key = "tsx" if suffix == ".tsx" else "ts"
    cached = _LANGUAGES.get(key)
    if cached is None:
        tree_sitter, tree_sitter_typescript = _require_tree_sitter()
        grammar = (
            tree_sitter_typescript.language_tsx()
            if key == "tsx"
            else tree_sitter_typescript.language_typescript()
        )
        cached = tree_sitter.Language(grammar)
        _LANGUAGES[key] = cached
    return cached


def is_generated_typescript(path: Path, source_head: str) -> bool:
    """Generated/vendored code is excluded from discovery, mirroring how generated Python
    is kept out of scoring. Detected by filename (`*.pb.ts` / `*.gen.ts`) or an
    `@generated` marker in the file header."""
    if _GENERATED_NAME_RE.search(path.name):
        return True
    return _GENERATED_HEADER_MARKER in source_head


def discover_typescript(path: Path, *, root: Path) -> list[TsFunction]:
    """Discover functions in a single `.ts`/`.tsx` file. Returns [] for generated files."""
    source = path.read_bytes()
    head = source[:2000].decode("utf-8", "replace")
    if is_generated_typescript(path, head):
        return []
    tree_sitter, _ = _require_tree_sitter()
    parser = tree_sitter.Parser(_language(path.suffix))
    tree = parser.parse(source)
    rel = _relative_posix(path, root)
    found: list[TsFunction] = []
    for node in _walk(tree.root_node):
        fn = _function_from_node(node, rel)
        if fn is not None:
            found.append(fn)
    found.sort(key=lambda f: (f.span.start_line, f.id.qualname))
    return found


def _walk(root: Node) -> list[Node]:
    out: list[Node] = []
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        out.append(node)
        stack.extend(node.children)
    return out


def _ident_text(node: Node | None) -> str | None:
    if node is None or node.text is None:
        return None
    return node.text.decode("utf-8")


def _declarator_name(node: Node) -> str | None:
    """Name of a const/let-assigned arrow or function expression (None for anonymous)."""
    parent = node.parent
    if parent is None or parent.type != "variable_declarator":
        return None
    return _ident_text(parent.child_by_field_name("name"))


def _scope_name(node: Node) -> str | None:
    """The qualname segment a node contributes (class/function/method name, or the name a
    function-valued declarator binds), or None if it introduces no named scope."""
    t = node.type
    if t in ("class_declaration", "function_declaration", "method_definition"):
        return _ident_text(node.child_by_field_name("name"))
    if t == "variable_declarator":
        value = node.child_by_field_name("value")
        if value is not None and value.type in ("arrow_function", "function_expression"):
            return _ident_text(node.child_by_field_name("name"))
    return None


def _qualname(node: Node, own_name: str) -> str:
    parts: list[str] = []
    ancestor = node.parent
    while ancestor is not None:
        # Skip the declarator that supplies this node's own name (arrows/func exprs),
        # so it isn't counted twice.
        own_declarator = False
        if ancestor.type == "variable_declarator":
            value = ancestor.child_by_field_name("value")
            own_declarator = value is not None and value.id == node.id
        if not own_declarator:
            name = _scope_name(ancestor)
            if name is not None:
                parts.append(name)
        ancestor = ancestor.parent
    parts.reverse()
    parts.append(own_name)
    return ".".join(parts)


def _is_exported_decl(node: Node) -> bool:
    """True if the declaration is a top-level statement wrapped in `export`/`export default`."""
    decl: Node | None = node
    if node.type in ("arrow_function", "function_expression"):
        declarator = node.parent
        decl = declarator.parent if declarator is not None else None  # lexical_declaration
    if decl is None:
        return False
    parent = decl.parent
    return parent is not None and parent.type == "export_statement"


def _enclosing_class(node: Node) -> Node | None:
    ancestor = node.parent
    while ancestor is not None:
        if ancestor.type == "class_declaration":
            return ancestor
        ancestor = ancestor.parent
    return None


def _method_is_private(node: Node) -> bool:
    name = node.child_by_field_name("name")
    if name is not None and name.type == "private_property_identifier":
        return True
    return any(
        child.type == "accessibility_modifier" and _node_text(child) in ("private", "protected")
        for child in node.children
    )


def _node_text(node: Node) -> str:
    return node.text.decode("utf-8") if node.text is not None else ""


def _is_public(node: Node) -> bool:
    if node.type == "method_definition":
        if _method_is_private(node):
            return False
        cls = _enclosing_class(node)
        return cls is not None and _is_exported_decl(cls)
    return _is_exported_decl(node)


def _is_async(node: Node) -> bool:
    return any(child.type == "async" for child in node.children)


def _function_from_node(node: Node, rel_path: str) -> TsFunction | None:
    t = node.type
    if t not in _FUNCTION_KINDS and t != "method_definition":
        return None

    if t == "method_definition":
        if node.parent is None or node.parent.type != "class_body":
            return None  # object-literal method, not a class method
        own = _ident_text(node.child_by_field_name("name"))
        kind = "method"
    elif t == "function_declaration":
        own = _ident_text(node.child_by_field_name("name"))
        kind = "function"
    elif t == "arrow_function":
        own = _declarator_name(node)  # None for anonymous inline callbacks → skipped
        kind = "arrow"
    else:  # function_expression
        own = _declarator_name(node)
        kind = "function"

    if own is None:
        return None

    return TsFunction(
        id=FunctionId(path=rel_path, qualname=_qualname(node, own)),
        span=FunctionSpan(
            start_line=int(node.start_point[0]) + 1,
            end_line=int(node.end_point[0]) + 1,
        ),
        is_public=_is_public(node),
        is_async=_is_async(node),
        kind=kind,
    )
