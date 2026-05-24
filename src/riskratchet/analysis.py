"""AST-based discovery of functions, their qualified names, and file stats.

A `ParsedFile` is the single source of truth for a file in one analysis run.
Downstream modules (complexity, coverage, scoring) read from it rather than
re-parsing.
"""

from __future__ import annotations

import ast
import copy
import hashlib
from dataclasses import dataclass
from pathlib import Path

from riskratchet.models import FileStats, FunctionId, FunctionSpan


@dataclass(frozen=True, slots=True)
class DiscoveredFunction:
    id: FunctionId
    span: FunctionSpan
    is_public: bool
    is_async: bool
    fingerprint: str
    node: ast.FunctionDef | ast.AsyncFunctionDef


@dataclass
class ParsedFile:
    path: Path
    relative_path: str
    source: str
    tree: ast.Module
    file_stats: FileStats
    functions: tuple[DiscoveredFunction, ...]


@dataclass(frozen=True, slots=True)
class ParseError:
    path: Path
    message: str


def parse_file(path: Path, *, root: Path) -> ParsedFile | ParseError:
    """Parse a Python file and discover its functions.

    Returns a `ParseError` (rather than raising) when the file has a syntax
    error or is unreadable. Skipping at the boundary keeps the engine simple:
    it filters out errors instead of catching them everywhere.
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return ParseError(path=path, message=f"cannot read file: {exc}")

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return ParseError(path=path, message=f"syntax error: {exc.msg} (line {exc.lineno})")

    relative_path = _relative_posix(path, root)
    functions = _discover_functions(tree, relative_path)
    file_stats = FileStats(
        path=relative_path,
        total_lines=_count_lines(source),
        function_count=len(functions),
    )
    return ParsedFile(
        path=path,
        relative_path=relative_path,
        source=source,
        tree=tree,
        file_stats=file_stats,
        functions=functions,
    )


def _relative_posix(path: Path, root: Path) -> str:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        rel = path
    return rel.as_posix()


def _count_lines(source: str) -> int:
    if not source:
        return 0
    return source.count("\n") + (0 if source.endswith("\n") else 1)


def _is_private_segment(segment: str) -> bool:
    """Return True for `_foo` style names; False for `__init__` dunders."""
    if not segment.startswith("_"):
        return False
    return not (segment.startswith("__") and segment.endswith("__") and len(segment) > 4)


def is_public_qualname(qualname: str) -> bool:
    return not any(_is_private_segment(seg) for seg in qualname.split("."))


def _extract_dunder_all(tree: ast.Module) -> frozenset[str] | None:
    """Return module-level `__all__` as a frozenset, or None if absent/dynamic.

    Only a single static `__all__ = [...]` (or tuple) of string literals is
    recognised. Augmented assignment, multiple assignments, concatenation,
    conditional assignment, or any non-literal element all yield None —
    callers must then fall back to the qualname-based naming rule.
    """
    found: frozenset[str] | None = None
    for node in tree.body:
        if isinstance(node, ast.AugAssign) and _is_dunder_all_target(node.target):
            return None
        if not isinstance(node, ast.Assign):
            continue
        if not any(_is_dunder_all_target(t) for t in node.targets):
            continue
        if found is not None:
            return None  # multiple assignments: treat as dynamic
        if len(node.targets) != 1:
            return None
        value = node.value
        if not isinstance(value, (ast.List, ast.Tuple)):
            return None
        names: list[str] = []
        for element in value.elts:
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                names.append(element.value)
            else:
                return None
        found = frozenset(names)
    return found


def _is_dunder_all_target(node: ast.expr) -> bool:
    return isinstance(node, ast.Name) and node.id == "__all__"


def _compute_is_public(qualname: str, dunder_all: frozenset[str] | None) -> bool:
    """Additive `__all__` semantics: listing promotes the top-level segment.

    `__all__` can promote a leading-underscore top-level name (e.g. a
    `_LegacyExposed` class kept in `__all__` for backwards compatibility)
    to public, but it does not affect nested segments: a `_helper` method
    on a promoted class is still private. Omission from `__all__` never
    demotes a name — `__all__` only controls `import *`, not reachability.
    """
    segments = qualname.split(".")
    if dunder_all is not None and segments[0] in dunder_all:
        top_public = True
    else:
        top_public = not _is_private_segment(segments[0])
    if not top_public:
        return False
    return not any(_is_private_segment(seg) for seg in segments[1:])


class _FunctionCollector(ast.NodeVisitor):
    def __init__(self, relative_path: str, dunder_all: frozenset[str] | None) -> None:
        self._stack: list[str] = []
        self._relative_path = relative_path
        self._dunder_all = dunder_all
        self.functions: list[DiscoveredFunction] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._stack.append(node.name)
        try:
            self.generic_visit(node)
        finally:
            self._stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record(node)

    def _record(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualname = ".".join([*self._stack, node.name])
        span = FunctionSpan(
            start_line=node.lineno,
            end_line=node.end_lineno or node.lineno,
        )
        self.functions.append(
            DiscoveredFunction(
                id=FunctionId(path=self._relative_path, qualname=qualname),
                span=span,
                is_public=_compute_is_public(qualname, self._dunder_all),
                is_async=isinstance(node, ast.AsyncFunctionDef),
                fingerprint=function_fingerprint(node),
                node=node,
            )
        )
        self._stack.append(node.name)
        try:
            self.generic_visit(node)
        finally:
            self._stack.pop()


def _discover_functions(tree: ast.Module, relative_path: str) -> tuple[DiscoveredFunction, ...]:
    dunder_all = _extract_dunder_all(tree)
    collector = _FunctionCollector(relative_path, dunder_all)
    collector.visit(tree)
    return tuple(collector.functions)


def iter_python_files(
    paths: list[Path],
    *,
    root: Path,
    exclude: list[str] | None = None,
    include: list[str] | None = None,
) -> list[Path]:
    """Walk `paths` and return matching .py files.

    `include`/`exclude` are glob patterns matched against the root-relative
    POSIX path. Files inside hidden directories (dotfile parents) are skipped.
    """
    exclude = exclude or []
    include = include or []
    seen: set[Path] = set()
    out: list[Path] = []
    for entry in paths:
        for path in _walk_python(entry):
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


def _walk_python(entry: Path) -> list[Path]:
    if entry.is_file():
        return [entry] if entry.suffix == ".py" else []
    if not entry.is_dir():
        return []
    return [p for p in entry.rglob("*.py") if not _has_hidden_parent(p, entry)]


def _has_hidden_parent(path: Path, root: Path) -> bool:
    return any(part.startswith(".") for part in path.relative_to(root).parts[:-1])


def _any_match(value: str, patterns: list[str]) -> bool:
    from fnmatch import fnmatch

    return any(fnmatch(value, pattern) for pattern in patterns)


def function_fingerprint(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Return a stable body fingerprint that ignores name and source locations."""
    clone = copy.deepcopy(node)
    clone.name = ""
    for child in ast.walk(clone):
        for attr in ("lineno", "col_offset", "end_lineno", "end_col_offset"):
            if hasattr(child, attr):
                setattr(child, attr, None)
    dumped = ast.dump(clone, annotate_fields=True, include_attributes=False)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()
