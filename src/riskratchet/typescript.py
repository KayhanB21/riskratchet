"""EXPERIMENTAL TypeScript function discovery (P20, slice 2, since 0.2.12).

Discovery only — no scoring, no baseline, no gating. The output is informational and its
shape may change. The Python analyzer is unaffected: this module is never imported by
`engine.analyze`; it is reached solely through the `scan --experimental-typescript` path.

Per-function coverage is *not* computed here — discovery stays parser-only. Slice 3
(`0.2.13`) maps Istanbul `coverage-final.json` onto the spans this module returns; that lives
in `typescript_coverage.py` and is attached to `TsFunction.coverage` by the CLI when
`--ts-coverage` is given.

Parsing uses tree-sitter, pulled in by the optional `typescript` extra
(`pip install 'riskratchet[typescript]'`). A default Python-only install never imports
it. The tree-sitter node taxonomy this relies on was confirmed by a spike against
`tests/fixtures/typescript/` (see `docs/typescript-parser-decision.md`).

Discovered: top-level function declarations, class methods (incl. on abstract and
anonymous default-export classes), and named (const/let-assigned) arrow and function
expressions, with React function components falling out naturally as exported
functions/arrows. Qualnames reflect nesting through classes, functions, and
`namespace`/`module` blocks, so a namespaced `Foo.bar` never collides with a top-level
`bar`. Public surface is export reachability — inline `export`/`export default` *and*
separate `export { name }` clauses. Files containing tree-sitter ERROR nodes are skipped
(optionally reported via `on_error`), mirroring how the Python backend skips unparseable
files rather than emitting partial results.

Deliberately skipped: anonymous inline callbacks (e.g. `xs.map(x => …)`), object-literal
methods, interface/abstract method *signatures* (no body), and generated files
(`@generated` comment header or `*.pb.ts` / `*.gen.ts` name, incl. `.mts`/`.cts`). Not yet
supported (silently skipped): generator functions and async iterators.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ._paths import any_match as _any_match
from ._paths import has_hidden_parent as _has_hidden_parent
from ._paths import relative_posix as _relative_posix
from .models import ComplexityStats, CoverageStats, FunctionId, FunctionSpan
from .typescript_complexity import cyclomatic_for_node
from .typescript_exports import Forward, Local, ModuleExports, resolve_specifier

if TYPE_CHECKING:  # annotations only; tree-sitter is an optional runtime import
    from collections.abc import Callable

    from tree_sitter import Node

_INSTALL_HINT = "TypeScript discovery needs the optional extra: pip install 'riskratchet[typescript]'"
_TS_SUFFIXES = (".ts", ".tsx", ".mts", ".cts")
_GENERATED_NAME_RE = re.compile(r"\.(pb|gen)\.[mc]?tsx?$")
# Line-anchored: `@generated` only counts as a generated-file marker inside a leading
# comment (`//`, `/*`, or a `*` continuation line), not anywhere it appears in a string
# or prose. Scanned over the file head only (first 2000 bytes).
_GENERATED_HEADER_RE = re.compile(r"^\s*(?://+|/\*+|\*)\s*@generated", re.MULTILINE)
_FUNCTION_KINDS = frozenset({"function_declaration", "function_expression", "arrow_function"})
# Class node types: `class_declaration` (named), `abstract_class_declaration` (`abstract
# class`), and `class` (a class *expression*, e.g. `export default class {}`).
_CLASS_KINDS = frozenset({"class_declaration", "abstract_class_declaration", "class"})
# `namespace Foo {}` parses as `internal_module`; `module Foo {}` as `module`.
_NAMESPACE_KINDS = frozenset({"internal_module", "module"})

_LANGUAGES: dict[str, Any] = {}


def _require_tree_sitter() -> tuple[Any, Any]:
    try:
        import tree_sitter
        import tree_sitter_typescript
    except ImportError as exc:  # tested via tests/test_typescript_absent_extra.py
        raise ImportError(_INSTALL_HINT) from exc
    return tree_sitter, tree_sitter_typescript


@dataclass(frozen=True, slots=True)
class TsFunction:
    """A function discovered in a TypeScript source file. Language-neutral shape, reusing
    `FunctionId`/`FunctionSpan`/`CoverageStats`; no fingerprint/score (discovery is
    informational). `coverage` is None until enriched from an Istanbul report
    (`typescript_coverage.coverage_for_ts_span`); discovery itself never sets it.

    Conforms to `models.DiscoveredFunctionLike` — the shared backend protocol it satisfies
    alongside the Python `analysis.DiscoveredFunction` (proven in
    `tests/test_backend_protocol.py`). The structural shapes are now unified behind that one
    protocol. What is still missing before TypeScript can enter the scoring/baseline pipeline
    is **identity**: a token-stable body/signature fingerprint for rename-aware matching, which
    Python carries on `DiscoveredFunction` but tree-sitter discovery does not yet produce. That
    identity work — not the shape — is the remaining slice-5 dependency."""

    id: FunctionId
    span: FunctionSpan
    is_public: bool
    is_async: bool
    kind: str  # "function" | "method" | "arrow"
    # McCabe cyclomatic (since 0.2.14), computed at discovery from the live tree-sitter node.
    complexity: ComplexityStats | None = None
    coverage: CoverageStats | None = None


def iter_typescript_files(
    paths: list[Path],
    *,
    root: Path,
    exclude: list[str] | None = None,
    include: list[str] | None = None,
) -> list[Path]:
    """Walk `paths` and return matching `.ts`/`.tsx`/`.mts`/`.cts` files (mirrors
    `iter_python_files`)."""
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
    # `.tsx` needs the JSX-aware grammar; `.ts`/`.mts`/`.cts` use the plain TS grammar
    # (`.mts`/`.cts` cannot contain JSX).
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
    is kept out of scoring. Detected by filename (`*.pb.ts` / `*.gen.ts`, incl.
    `.mts`/`.cts`) or a comment-anchored `@generated` marker in the file header."""
    if _GENERATED_NAME_RE.search(path.name):
        return True
    return _GENERATED_HEADER_RE.search(source_head) is not None


def discover_typescript(
    path: Path,
    *,
    root: Path,
    on_error: Callable[[Path, str], None] | None = None,
) -> list[TsFunction]:
    """Discover functions in a single `.ts`/`.tsx`/`.mts`/`.cts` file.

    Returns [] for generated files. If tree-sitter reports ERROR nodes (a genuinely
    broken file), the whole file is skipped and `on_error(path, "syntax error")` is
    invoked when provided — partial/garbage results are never emitted, matching the
    Python backend's "skip unparseable files" behaviour.
    """
    source = path.read_bytes()
    head = source[:2000].decode("utf-8", "replace")
    if is_generated_typescript(path, head):
        return []
    tree_sitter, _ = _require_tree_sitter()
    parser = tree_sitter.Parser(_language(path.suffix))
    tree = parser.parse(source)
    if tree.root_node.has_error:
        if on_error is not None:
            on_error(path, "syntax error")
        return []
    rel = _relative_posix(path, root)
    exported = _collect_exported_names(tree.root_node)
    found: list[TsFunction] = []
    for node in _walk(tree.root_node):
        fn = _function_from_node(node, rel, exported)
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


def _node_text(node: Node) -> str:
    return node.text.decode("utf-8") if node.text is not None else ""


def _declarator_name(node: Node) -> str | None:
    """Name of a const/let-assigned arrow or function expression (None for anonymous)."""
    parent = node.parent
    if parent is None or parent.type != "variable_declarator":
        return None
    return _ident_text(parent.child_by_field_name("name"))


def _anon_class_name(node: Node) -> str:
    """A stable segment for a class with no name field, so its methods don't silently
    merge into the file's top-level namespace. `export default class {}` → `default`;
    `const C = class {}` → `C`; otherwise `<anonymous>`."""
    parent = node.parent
    if parent is not None:
        if parent.type == "export_statement" and any(c.type == "default" for c in parent.children):
            return "default"
        if parent.type == "variable_declarator":
            bound = _ident_text(parent.child_by_field_name("name"))
            if bound is not None:
                return bound
    return "<anonymous>"


def _scope_name(node: Node) -> str | None:
    """The qualname segment a node contributes (class / function / method / namespace
    name, or the name a function-valued declarator binds), or None if it introduces no
    named scope."""
    t = node.type
    if t in _CLASS_KINDS:
        return _ident_text(node.child_by_field_name("name")) or _anon_class_name(node)
    if t in ("function_declaration", "method_definition") or t in _NAMESPACE_KINDS:
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


def _collect_exported_names(root: Node) -> set[str]:
    """Local names made public by a top-level `export { name }` / `export { name as default }`
    clause (the *local* name, not the alias), or by `export default <identifier>` referencing a
    separately-declared binding. Complements inline `export`/`export default`."""
    names: set[str] = set()
    for child in root.children:
        if child.type != "export_statement":
            continue
        for clause in child.children:
            if clause.type != "export_clause":
                continue
            for spec in clause.children:
                if spec.type == "export_specifier":
                    local = _ident_text(spec.child_by_field_name("name"))
                    if local is not None:
                        names.add(local)
        # `export default myFunc;` — the default-exported binding is public even though the
        # declaration itself carries no `export` keyword (fixed in 0.2.14).
        default_ident = _default_export_identifier(child)
        if default_ident is not None:
            names.add(default_ident)
    return names


def _default_export_identifier(stmt: Node) -> str | None:
    """The bound name of a bare `export default <identifier>;` (else None)."""
    if not any(c.type == "default" for c in stmt.children):
        return None
    value = stmt.child_by_field_name("value")
    if value is not None and value.type == "identifier":
        return _node_text(value)
    return None


def parse_module_exports(path: Path, *, root: Path) -> ModuleExports:
    """Parse one file's top-level export surface (for barrel resolution in
    `typescript_exports`). Returns an empty surface for generated or unparseable files, matching
    discovery. This is a second tree-sitter parse of the file, separate from
    `discover_typescript`'s — acceptable for this experimental, informational-only path.
    """
    result = ModuleExports()
    source = path.read_bytes()
    head = source[:2000].decode("utf-8", "replace")
    if is_generated_typescript(path, head):
        return result
    tree_sitter, _ = _require_tree_sitter()
    parser = tree_sitter.Parser(_language(path.suffix))
    tree = parser.parse(source)
    if tree.root_node.has_error:
        return result
    for child in tree.root_node.children:
        if child.type == "export_statement":
            _classify_export(child, result)
    return result


def _classify_export(stmt: Node, result: ModuleExports) -> None:
    source = stmt.child_by_field_name("source")
    clause = next((c for c in stmt.children if c.type == "export_clause"), None)
    if clause is not None:
        spec = _string_literal(source) if source is not None else None
        for member in clause.children:
            if member.type == "export_specifier":
                _classify_specifier(member, spec, result)
        return
    if source is not None:  # `export * from '…'` / `export * as ns from '…'` (no clause)
        spec = _string_literal(source)
        if spec is not None:
            result.stars.append(spec)
        return
    is_default = any(c.type == "default" for c in stmt.children)
    decl = stmt.child_by_field_name("declaration")
    if decl is not None:
        _classify_declaration(decl, is_default, result)
        return
    default_ident = _default_export_identifier(stmt)  # `export default <identifier>;`
    if default_ident is not None:
        result.exports["default"] = Local(default_ident)


def _classify_specifier(member: Node, module_spec: str | None, result: ModuleExports) -> None:
    name = _ident_text(member.child_by_field_name("name"))
    if name is None:
        return
    exported_as = _ident_text(member.child_by_field_name("alias")) or name
    if module_spec is None:
        result.exports[exported_as] = Local(name)
    else:
        result.exports[exported_as] = Forward(module_spec, name)


def _classify_declaration(decl: Node, is_default: bool, result: ModuleExports) -> None:
    if decl.type in ("lexical_declaration", "variable_declaration"):
        for child in decl.children:
            if child.type == "variable_declarator":
                name = _ident_text(child.child_by_field_name("name"))
                if name is not None:
                    result.exports[name] = Local(name)
        return
    name = _ident_text(decl.child_by_field_name("name"))
    if name is None:
        # `export default function(){}` / `export default class {}` — the anonymous default
        # class discovers methods under a `default` qualname segment (`_anon_class_name`).
        if is_default:
            result.exports["default"] = Local("default")
        return
    result.exports["default" if is_default else name] = Local(name)


def _string_literal(node: Node) -> str | None:
    raw = _node_text(node)
    if len(raw) >= 2 and raw[0] in "'\"`" and raw[-1] == raw[0]:
        return raw[1:-1]
    return raw or None


def detect_ts_entries(root: Path, files: list[Path], explicit: list[Path]) -> list[str]:
    """Resolve package entry file(s) to relative-POSIX keys within `files`, in priority order:
    explicit `--ts-entry`, then `package.json` `exports`/`module`/`main`/`types`, then the
    shallowest `index.{ts,tsx,mts,cts}`. Returns [] when nothing resolves — the caller then
    keeps file-level export flags (no narrowing)."""
    by_abs: dict[Path, str] = {}
    keys: set[str] = set()
    for path in files:
        key = _relative_posix(path, root)
        keys.add(key)
        by_abs[path.resolve()] = key
    if explicit:
        out: list[str] = []
        for entry in explicit:
            abs_entry = (entry if entry.is_absolute() else Path.cwd() / entry).resolve()
            matched = by_abs.get(abs_entry)
            if matched is not None:
                out.append(matched)
        return out
    from_package = _package_json_entries(root, keys)
    if from_package:
        return from_package
    return _index_entries(keys)


def _package_json_entries(root: Path, keys: set[str]) -> list[str]:
    manifest = root / "package.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    out: list[str] = []
    for spec in _package_json_specs(data):
        resolved = resolve_specifier("package.json", spec, keys)
        if resolved is not None and resolved not in out:
            out.append(resolved)
    return out


def _package_json_specs(data: dict[str, Any]) -> list[str]:
    specs = _exports_field_specs(data.get("exports"))
    for field_name in ("module", "main", "types", "typings"):
        value = data.get(field_name)
        if isinstance(value, str):
            specs.append(value)
    return specs


def _exports_field_specs(exports: Any) -> list[str]:
    """Entry specifiers from a package.json `exports` field: a bare string, `exports["."]`, or
    the conditions of `exports["."]` (`types`/`import`/`default`/`node`)."""
    if isinstance(exports, str):
        return [exports]
    if not isinstance(exports, dict):
        return []
    root_export = exports.get(".", exports)
    if isinstance(root_export, str):
        return [root_export]
    if not isinstance(root_export, dict):
        return []
    return [v for k in ("types", "import", "default", "node") if isinstance(v := root_export.get(k), str)]


def _index_entries(keys: set[str]) -> list[str]:
    indexes = [k for k in keys if k.rsplit("/", 1)[-1] in _INDEX_NAMES]
    if not indexes:
        return []
    min_depth = min(k.count("/") for k in indexes)
    return sorted(k for k in indexes if k.count("/") == min_depth)


_INDEX_NAMES = frozenset({"index.ts", "index.tsx", "index.mts", "index.cts"})


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


def _own_binding_name(node: Node) -> str | None:
    """The name a discoverable declaration binds at module scope (for export-clause lookup)."""
    t = node.type
    if t in ("arrow_function", "function_expression"):
        return _declarator_name(node)
    if t in _CLASS_KINDS or t == "function_declaration":
        return _ident_text(node.child_by_field_name("name"))
    return None


def _enclosing_class(node: Node) -> Node | None:
    ancestor = node.parent
    while ancestor is not None:
        if ancestor.type in _CLASS_KINDS:
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


def _is_public(node: Node, exported: set[str]) -> bool:
    """Export reachability: a declaration is public if it is inline-exported or its bound
    name appears in a top-level `export { … }` clause. Methods inherit their class's
    surface (and a `private`/`protected`/`#name` method is never public)."""
    if node.type == "method_definition":
        if _method_is_private(node):
            return False
        cls = _enclosing_class(node)
        if cls is None:
            return False
        return _is_exported_decl(cls) or (_own_binding_name(cls) in exported)
    return _is_exported_decl(node) or (_own_binding_name(node) in exported)


def _is_async(node: Node) -> bool:
    # The `async` keyword is a direct child of function_declaration, arrow_function,
    # function_expression, and method_definition alike (confirmed against the grammar).
    return any(child.type == "async" for child in node.children)


def _function_from_node(node: Node, rel_path: str, exported: set[str]) -> TsFunction | None:
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
        is_public=_is_public(node, exported),
        is_async=_is_async(node),
        kind=kind,
        complexity=ComplexityStats(cyclomatic=cyclomatic_for_node(node)),
    )
