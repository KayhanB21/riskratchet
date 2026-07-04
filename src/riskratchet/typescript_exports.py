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


@dataclass(frozen=True, slots=True)
class EntryReachability:
    """Result of walking the re-export graph from the package entry.

    `reachable` is the set of `(file, local_name)` declarations reachable from an entry. The
    caller demotes a public function to internal only when its binding is *not* reachable — and
    only when the graph is trustworthy, per the two guards below.

    `poison_all` — an **unresolved wildcard** (`export * from <unresolved>`) reachable from an
    entry, or an entry file not in the scanned set. A wildcard could re-export any name (incl. a
    tsconfig-aliased one back into the set), so the surface can't be bounded and nothing is
    demoted.

    `uncertain_names` — the source-names of **unresolved named** re-exports
    (`export { srcName } from <unresolved>`). Only a function bound to one of these names could
    be alias-exposed, so only those are held public; everything else still narrows.
    """

    reachable: set[tuple[str, str]]
    poison_all: bool
    uncertain_names: set[str]


def resolve_entry_reachable(modules: dict[str, ModuleExports], entries: list[str]) -> EntryReachability:
    """Walk the re-export graph from `entries` and return an `EntryReachability`. See that type
    for how `reachable` / `poison_all` / `uncertain_names` gate the caller's narrowing."""
    return _Reachability(modules).run(entries)


class _Reachability:
    """Worklist traversal of the re-export graph. Kept as a small state object so each step
    stays simple. Unresolved wildcards/entries set `poison_all`; unresolved named re-exports add
    to `uncertain_names`."""

    def __init__(self, modules: dict[str, ModuleExports]) -> None:
        self.modules = modules
        self.keys = set(modules)
        self.reachable: set[tuple[str, str]] = set()
        self.poison_all = False
        self.uncertain_names: set[str] = set()
        self._seen_all: set[tuple[str, bool]] = set()
        self._seen_name: set[tuple[str, str]] = set()
        # Items are ("all", file, include_default) or ("name", file, export_name).
        self._work: list[tuple[str, str, object]] = []

    def run(self, entries: list[str]) -> EntryReachability:
        for entry in entries:
            if entry in self.keys:
                self._work.append(("all", entry, True))
            else:
                self.poison_all = True  # can't establish the surface from a missing entry
        while self._work:
            kind, file, payload = self._work.pop()
            if kind == "all":
                self._expand_all(file, bool(payload))
            else:
                self._resolve_name(file, str(payload))
        return EntryReachability(self.reachable, self.poison_all, self.uncertain_names)

    def _expand_all(self, file: str, include_default: bool) -> None:
        if (file, include_default) in self._seen_all:
            return
        self._seen_all.add((file, include_default))
        mod = self.modules[file]  # file came from self.keys / a resolved specifier
        for name in mod.exports:
            if name == "default" and not include_default:
                continue  # `export *` never re-exports the default
            self._work.append(("name", file, name))
        for spec in mod.stars:  # `export *` re-exports the whole target
            target = self._resolve_star(file, spec)
            if target is not None:
                self._work.append(("all", target, False))

    def _resolve_name(self, file: str, name: str) -> None:
        if (file, name) in self._seen_name:
            return
        self._seen_name.add((file, name))
        mod = self.modules[file]
        origin = mod.exports.get(name)
        if origin is None:
            self._resolve_through_stars(file, name, mod)  # may come from an `export * from`
            return
        if isinstance(origin, Local):
            self.reachable.add((file, origin.name))
            return
        target = resolve_specifier(file, origin.specifier, self.keys)
        if target is None:
            self.uncertain_names.add(origin.source_name)  # only this name might be alias-exposed
        else:
            self._work.append(("name", target, origin.source_name))

    def _resolve_through_stars(self, file: str, name: str, mod: ModuleExports) -> None:
        # The name may be re-exported by an `export * from`. Forward just this name (not the whole
        # target) so a named re-export from a barrel-of-stars doesn't over-widen. Default is never
        # carried by a star.
        for spec in mod.stars:
            target = self._resolve_star(file, spec)
            if target is not None and name != "default":
                self._work.append(("name", target, name))

    def _resolve_star(self, file: str, spec: str) -> str | None:
        # An unresolved wildcard could expose any name under any local binding, so we cannot bound
        # the surface — poison it rather than guess. Returns the target key, or None if unresolved.
        target = resolve_specifier(file, spec, self.keys)
        if target is None:
            self.poison_all = True
        return target
