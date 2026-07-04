"""EXPERIMENTAL: barrel-aware public-surface resolution for TypeScript (P20, slice 4,
since 0.2.14).

Discovery (`typescript.py`) marks a function `is_public` when it is exported *from its own
file*. But a package's real public surface is what consumers can reach through its **entry
barrel** (`index.ts`, or `package.json` `exports`/`module`/`main`). A function that is
file-exported but never re-exported to an entry is effectively module-internal.

This module resolves that. Given each file's export table (`ModuleExports`, produced by
`typescript.parse_module_exports`) and the package entry file(s), it walks the re-export graph
(`export { x } from './m'`, `export * from './m'`, transitively) and returns the set of
`(file, local_name)` declarations reachable from an entry. The caller narrows `is_public`:
a currently-public function whose binding is *not* entry-reachable becomes internal.

**Safety rail — narrowing never fires on an unproven graph.** `resolve_entry_reachable`
returns a `complete` flag that is False whenever a re-export specifier cannot be resolved to a
file in the scanned set (a bare/`node_modules` import, a tsconfig-`paths` alias, an entry
outside the set). The caller must keep the file-level export flags when `complete` is False —
so an unresolved chain, or a project with no barrel at all, keeps the pre-0.2.14 behaviour and
we never assert "internal" on a function we merely failed to trace.

Pure over `ModuleExports` and POSIX-relative path strings — imports no tree-sitter, so it is
unit-testable without the extra.

Out of scope (documented in `docs/language-backend-contract.md §4`): tsconfig
`paths`/`baseUrl`, `node_modules`, dynamic `import()`, and declaration merging.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath

_TS_EXTS = (".ts", ".tsx", ".mts", ".cts")
_JS_EXTS = (".js", ".jsx", ".mjs", ".cjs")


@dataclass(frozen=True, slots=True)
class Local:
    """An exported name backed by a declaration in this file, bound as `name`."""

    name: str


@dataclass(frozen=True, slots=True)
class Forward:
    """An exported name re-exported from another module: `export { source_name } from spec`."""

    specifier: str
    source_name: str


@dataclass(slots=True)
class ModuleExports:
    """One file's export surface.

    `exports` maps each externally-visible export name to its origin (a local declaration or a
    forward to another module). `stars` holds the specifiers of `export * from '…'` (and
    `export * as ns from '…'`, treated as a star — the conservative, never-demote direction).
    """

    exports: dict[str, Local | Forward] = field(default_factory=dict)
    stars: list[str] = field(default_factory=list)


def resolve_specifier(importer_rel: str, specifier: str, module_keys: set[str]) -> str | None:
    """Resolve a relative module `specifier` seen in `importer_rel` to a file key in the scanned
    set, or None if it is bare (alias/`node_modules`) or resolves outside the set.

    Tries the TS extension ladder and `…/index.*`, and maps a `.js`/`.jsx`/`.mjs`/`.cjs`
    specifier (the NodeNext convention) back onto its TS source.
    """
    if not specifier.startswith("."):
        return None  # bare import — node_modules or a tsconfig alias we do not resolve
    target = _normalize(PurePosixPath(importer_rel).parent / specifier)
    return _match_module(target, module_keys)


def _normalize(path: PurePosixPath) -> str:
    parts: list[str] = []
    for part in path.parts:
        if part in (".", ""):
            continue
        if part == ".." and parts and parts[-1] not in ("..", "/"):
            parts.pop()
        else:
            parts.append(part)
    # `PurePosixPath("/a/b").parts` begins with "/"; join the rest onto a single leading slash
    # so an absolute path key does not become "//a/b".
    if parts and parts[0] == "/":
        return "/" + "/".join(parts[1:])
    return "/".join(parts)


def _match_module(target: str, module_keys: set[str]) -> str | None:
    for ext in _JS_EXTS:
        if target.endswith(ext):
            target = target[: -len(ext)]
            break
    if target.endswith(_TS_EXTS):
        return target if target in module_keys else None
    for ext in _TS_EXTS:
        if (candidate := target + ext) in module_keys:
            return candidate
    for ext in _TS_EXTS:
        if (candidate := f"{target}/index{ext}") in module_keys:
            return candidate
    return None


def resolve_entry_reachable(
    modules: dict[str, ModuleExports], entries: list[str]
) -> tuple[set[tuple[str, str]], bool]:
    """Return `(reachable_locals, complete)` for the given entry files.

    `reachable_locals` is the set of `(file, local_name)` declarations reachable from any entry
    through the re-export graph. `complete` is False when any specifier (an entry, a forward, or
    a star target) cannot be resolved within `modules` — the signal for the caller to skip
    narrowing rather than demote on an unproven graph.
    """
    return _Reachability(modules).run(entries)


class _Reachability:
    """Worklist traversal of the re-export graph. Kept as a small state object so each step
    stays simple; `complete` flips to False on the first unresolved edge."""

    def __init__(self, modules: dict[str, ModuleExports]) -> None:
        self.modules = modules
        self.keys = set(modules)
        self.reachable: set[tuple[str, str]] = set()
        self.complete = True
        self._seen_all: set[tuple[str, bool]] = set()
        self._seen_name: set[tuple[str, str]] = set()
        # Items are ("all", file, include_default) or ("name", file, export_name).
        self._work: list[tuple[str, str, object]] = []

    def run(self, entries: list[str]) -> tuple[set[tuple[str, str]], bool]:
        for entry in entries:
            if entry in self.keys:
                self._work.append(("all", entry, True))
            else:
                self.complete = False
        while self._work:
            kind, file, payload = self._work.pop()
            if kind == "all":
                self._expand_all(file, bool(payload))
            else:
                self._resolve_name(file, str(payload))
        return self.reachable, self.complete

    def _expand_all(self, file: str, include_default: bool) -> None:
        if (file, include_default) in self._seen_all:
            return
        self._seen_all.add((file, include_default))
        mod = self.modules[file]  # file came from self.keys / a resolved specifier
        for name in mod.exports:
            if name == "default" and not include_default:
                continue  # `export *` never re-exports the default
            self._work.append(("name", file, name))
        for spec in mod.stars:
            target = resolve_specifier(file, spec, self.keys)
            if target is None:
                self.complete = False
            else:
                self._work.append(("all", target, False))

    def _resolve_name(self, file: str, name: str) -> None:
        if (file, name) in self._seen_name:
            return
        self._seen_name.add((file, name))
        mod = self.modules[file]
        origin = mod.exports.get(name)
        if origin is None:
            self._resolve_through_stars(file, name, mod)
            return
        if isinstance(origin, Local):
            self.reachable.add((file, origin.name))
            return
        target = resolve_specifier(file, origin.specifier, self.keys)
        if target is None:
            self.complete = False
        else:
            self._work.append(("name", target, origin.source_name))

    def _resolve_through_stars(self, file: str, name: str, mod: ModuleExports) -> None:
        # A name not listed here may still be re-exported by an `export * from`. Default is
        # never carried by a star, so it is not chased through one.
        for spec in mod.stars:
            target = resolve_specifier(file, spec, self.keys)
            if target is None:
                self.complete = False
            elif name != "default":
                self._work.append(("name", target, name))
