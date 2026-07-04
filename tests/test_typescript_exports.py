"""Barrel-aware public-surface narrowing (P20 slice 4, since 0.2.14).

The graph logic in `typescript_exports` is pure (no tree-sitter) and tested directly. The
parse + end-to-end tests need the `typescript` extra and skip without it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from riskratchet.typescript_exports import (
    Forward,
    Local,
    ModuleExports,
    resolve_entry_reachable,
    resolve_specifier,
)

FIXTURES = Path(__file__).parent / "fixtures" / "typescript"
BARREL = FIXTURES / "barrel"


def _mod(exports: dict[str, object] | None = None, stars: list[str] | None = None) -> ModuleExports:
    return ModuleExports(exports=dict(exports or {}), stars=list(stars or []))  # type: ignore[arg-type]


# ---- pure specifier resolution --------------------------------------------------------------


def test_resolve_specifier_extension_ladder_and_index() -> None:
    keys = {"src/foo.ts", "src/bar/index.ts"}
    assert resolve_specifier("src/index.ts", "./foo", keys) == "src/foo.ts"
    assert resolve_specifier("src/index.ts", "./foo.js", keys) == "src/foo.ts"  # NodeNext .js→.ts
    assert resolve_specifier("src/index.ts", "./bar", keys) == "src/bar/index.ts"


def test_resolve_specifier_parent_traversal() -> None:
    keys = {"lib/x.ts"}
    assert resolve_specifier("src/index.ts", "../lib/x", keys) == "lib/x.ts"


def test_resolve_specifier_unresolvable_and_bare() -> None:
    keys = {"src/foo.ts"}
    assert resolve_specifier("src/index.ts", "./missing", keys) is None
    assert resolve_specifier("src/index.ts", "some-package", keys) is None  # bare import


# ---- pure reachability graph ----------------------------------------------------------------


def test_named_reexport_narrows_unreferenced_sibling() -> None:
    modules = {
        "index.ts": _mod({"exposed": Forward("./api", "exposed")}),
        "api.ts": _mod({"exposed": Local("exposed"), "hidden": Local("hidden")}),
    }
    reachable, complete = resolve_entry_reachable(modules, ["index.ts"])
    assert complete is True
    assert ("api.ts", "exposed") in reachable
    assert ("api.ts", "hidden") not in reachable


def test_aliased_reexport_maps_back_to_local_name() -> None:
    modules = {
        "index.ts": _mod({"Renamed": Forward("./api", "original")}),
        "api.ts": _mod({"original": Local("original")}),
    }
    reachable, _ = resolve_entry_reachable(modules, ["index.ts"])
    assert ("api.ts", "original") in reachable


def test_star_reexport_is_reachable_but_never_carries_default() -> None:
    modules = {
        "index.ts": _mod(stars=["./api"]),
        "api.ts": _mod({"a": Local("a"), "default": Local("d")}),
    }
    reachable, complete = resolve_entry_reachable(modules, ["index.ts"])
    assert complete is True
    assert ("api.ts", "a") in reachable
    assert ("api.ts", "d") not in reachable  # `export *` excludes the default export


def test_transitive_reexport_chain_resolves() -> None:
    modules = {
        "index.ts": _mod(stars=["./mid"]),
        "mid.ts": _mod({"leaf": Forward("./leaf", "leaf")}),
        "leaf.ts": _mod({"leaf": Local("leaf")}),
    }
    reachable, complete = resolve_entry_reachable(modules, ["index.ts"])
    assert complete is True
    assert ("leaf.ts", "leaf") in reachable


def test_entry_own_declarations_and_default_are_reachable() -> None:
    modules = {"index.ts": _mod({"top": Local("top"), "default": Local("makeThing")})}
    reachable, complete = resolve_entry_reachable(modules, ["index.ts"])
    assert complete is True
    assert ("index.ts", "top") in reachable
    assert ("index.ts", "makeThing") in reachable  # default IS part of the entry's own surface


def test_unresolved_forward_marks_incomplete() -> None:
    modules = {"index.ts": _mod({"x": Forward("./nope", "x")})}
    _reachable, complete = resolve_entry_reachable(modules, ["index.ts"])
    assert complete is False


def test_unresolved_star_marks_incomplete() -> None:
    modules = {"index.ts": _mod(stars=["./nope"])}
    _reachable, complete = resolve_entry_reachable(modules, ["index.ts"])
    assert complete is False


def test_entry_absent_from_modules_marks_incomplete() -> None:
    reachable, complete = resolve_entry_reachable({}, ["index.ts"])
    assert complete is False
    assert reachable == set()


# ---- entry detection (no tree-sitter needed) ------------------------------------------------


def test_exports_field_specs_variants() -> None:
    from riskratchet.typescript import _exports_field_specs

    assert _exports_field_specs("./index.ts") == ["./index.ts"]
    assert _exports_field_specs({".": "./main.ts"}) == ["./main.ts"]
    assert _exports_field_specs({".": {"import": "./m.ts", "types": "./t.ts"}}) == ["./t.ts", "./m.ts"]
    assert _exports_field_specs(None) == []
    assert _exports_field_specs(42) == []


def test_detect_ts_entries_prefers_package_json(tmp_path: Path) -> None:
    from riskratchet.typescript import detect_ts_entries

    (tmp_path / "package.json").write_text('{"module": "./src/api.ts"}', encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    api = src / "api.ts"
    api.write_text("export function x() {}\n", encoding="utf-8")
    index = src / "index.ts"
    index.write_text("", encoding="utf-8")
    assert detect_ts_entries(tmp_path, [api, index], []) == ["src/api.ts"]  # package.json wins


def test_detect_ts_entries_index_fallback_and_none(tmp_path: Path) -> None:
    from riskratchet.typescript import detect_ts_entries

    src = tmp_path / "src"
    src.mkdir()
    a = src / "a.ts"
    a.write_text("", encoding="utf-8")
    index = src / "index.ts"
    index.write_text("", encoding="utf-8")
    assert detect_ts_entries(tmp_path, [a, index], []) == ["src/index.ts"]
    assert detect_ts_entries(tmp_path, [a], []) == []  # no barrel at all → no entry


# ---- parse_module_exports (needs tree-sitter) -----------------------------------------------


def _parse(tmp_path: Path, name: str, src: str) -> ModuleExports:
    from riskratchet.typescript import parse_module_exports

    path = tmp_path / name
    path.write_text(src, encoding="utf-8")
    return parse_module_exports(path, root=tmp_path)


def test_parse_module_exports_named_inline_and_reexports(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    src = (
        "export function a() {}\n"
        "export const b = () => {};\n"
        "export class C {}\n"
        "function helper() {}\n"
        "export { helper as aliased };\n"
        "export { x as y } from './other';\n"
        "export * from './more';\n"
    )
    mod = _parse(tmp_path, "m.ts", src)
    assert mod.exports["a"] == Local("a")
    assert mod.exports["b"] == Local("b")
    assert mod.exports["C"] == Local("C")
    assert mod.exports["aliased"] == Local("helper")
    assert mod.exports["y"] == Forward("./other", "x")
    assert mod.stars == ["./more"]


def test_parse_module_exports_default_identifier_and_inline_default(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    ident = _parse(tmp_path, "d.ts", "function make() {}\nexport default make;\n")
    assert ident.exports["default"] == Local("make")
    inline = _parse(tmp_path, "e.ts", "export default function build() {}\n")
    assert inline.exports["default"] == Local("build")


def test_export_default_identifier_is_public_in_discovery(tmp_path: Path) -> None:
    # The 0.2.14 same-file fix: `export default make;` makes `make` public even though its
    # declaration carries no `export` keyword; an unexported sibling stays internal.
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    from riskratchet.typescript import discover_typescript

    path = tmp_path / "m.ts"
    path.write_text("function make() {}\nexport default make;\nfunction other() {}\n", encoding="utf-8")
    public = {fn.id.qualname: fn.is_public for fn in discover_typescript(path, root=tmp_path)}
    assert public == {"make": True, "other": False}


# ---- end-to-end CLI narrowing (needs tree-sitter) -------------------------------------------


def _isolated_barrel(tmp_path: Path) -> Path:
    """Copy the barrel fixtures outside the repo so config discovery doesn't pull in
    riskratchet's own `[tool.riskratchet]` (whose `exclude` would eat the fixtures)."""
    dest = tmp_path / "pkg"
    dest.mkdir()
    for name in ("index.ts", "public_api.ts", "helpers.ts", "internal.ts"):
        (dest / name).write_text((BARREL / name).read_text(encoding="utf-8"), encoding="utf-8")
    return dest


def _scan(app_dir: Path, monkeypatch: pytest.MonkeyPatch, *extra: str) -> Any:
    from typer.testing import CliRunner

    from riskratchet.cli import app

    monkeypatch.chdir(app_dir)
    return CliRunner().invoke(
        app, ["scan", ".", "--experimental-typescript", "--no-auto-cov", "--no-git", *extra]
    )


def test_barrel_narrowing_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    result = _scan(_isolated_barrel(tmp_path), monkeypatch)
    assert result.exit_code == 0, (result.stdout, result.stderr)
    err = result.stderr
    assert "exposed  [public]" in err  # re-exported by name
    assert "alsoExposed  [internal]" in err  # file-exported, not re-exported → narrowed
    assert "helper  [public]" in err  # via `export *`
    assert "hidden  [internal]" in err  # unreferenced module → narrowed
    assert "cx 1" in err  # complexity column present alongside


def test_no_entry_keeps_file_export_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    app_dir = _isolated_barrel(tmp_path)
    (app_dir / "index.ts").unlink()  # remove the only barrel → no entry → no narrowing
    result = _scan(app_dir, monkeypatch)
    assert result.exit_code == 0, (result.stdout, result.stderr)
    # Every file-exported function keeps its public flag (safety fallback).
    assert "alsoExposed  [public]" in result.stderr
    assert "hidden  [public]" in result.stderr


def test_incomplete_graph_warns_and_keeps_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    app_dir = _isolated_barrel(tmp_path)
    (app_dir / "index.ts").write_text(
        'export { exposed } from "./public_api";\nexport { z } from "external-pkg";\n',
        encoding="utf-8",
    )
    result = _scan(app_dir, monkeypatch)
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert "re-export graph is incomplete" in result.stderr
    assert "alsoExposed  [public]" in result.stderr  # not demoted on an unproven graph


def test_explicit_ts_entry_overrides_detection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    app_dir = _isolated_barrel(tmp_path)
    (app_dir / "index.ts").unlink()  # no auto entry; point --ts-entry at public_api.ts
    result = _scan(app_dir, monkeypatch, "--ts-entry", "public_api.ts")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    # public_api.ts is the entry, so both its exports are public; helpers/internal narrow.
    assert "exposed  [public]" in result.stderr
    assert "alsoExposed  [public]" in result.stderr
    assert "helper  [internal]" in result.stderr
    assert "hidden  [internal]" in result.stderr
