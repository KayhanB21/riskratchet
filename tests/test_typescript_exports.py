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
    res = resolve_entry_reachable(modules, ["index.ts"])
    assert res.poison_all is False
    assert ("api.ts", "exposed") in res.reachable
    assert ("api.ts", "hidden") not in res.reachable


def test_aliased_reexport_maps_back_to_local_name() -> None:
    modules = {
        "index.ts": _mod({"Renamed": Forward("./api", "original")}),
        "api.ts": _mod({"original": Local("original")}),
    }
    res = resolve_entry_reachable(modules, ["index.ts"])
    assert ("api.ts", "original") in res.reachable


def test_star_reexport_is_reachable_but_never_carries_default() -> None:
    modules = {
        "index.ts": _mod(stars=["./api"]),
        "api.ts": _mod({"a": Local("a"), "default": Local("d")}),
    }
    res = resolve_entry_reachable(modules, ["index.ts"])
    assert res.poison_all is False
    assert ("api.ts", "a") in res.reachable
    assert ("api.ts", "d") not in res.reachable  # `export *` excludes the default export


def test_transitive_reexport_chain_resolves() -> None:
    modules = {
        "index.ts": _mod(stars=["./mid"]),
        "mid.ts": _mod({"leaf": Forward("./leaf", "leaf")}),
        "leaf.ts": _mod({"leaf": Local("leaf")}),
    }
    res = resolve_entry_reachable(modules, ["index.ts"])
    assert res.poison_all is False
    assert ("leaf.ts", "leaf") in res.reachable


def test_named_through_barrel_of_stars_does_not_overwiden() -> None:
    # Importing one name from a barrel-of-stars must reach only that name, not everything.
    modules = {
        "index.ts": _mod({"wanted": Forward("./barrel", "wanted")}),
        "barrel.ts": _mod(stars=["./a", "./b"]),
        "a.ts": _mod({"wanted": Local("wanted"), "other": Local("other")}),
        "b.ts": _mod({"unrelated": Local("unrelated")}),
    }
    res = resolve_entry_reachable(modules, ["index.ts"])
    assert res.poison_all is False
    assert ("a.ts", "wanted") in res.reachable
    assert ("a.ts", "other") not in res.reachable  # not widened by the named-through-star lookup
    assert ("b.ts", "unrelated") not in res.reachable


def test_entry_own_declarations_and_default_are_reachable() -> None:
    modules = {"index.ts": _mod({"top": Local("top"), "default": Local("makeThing")})}
    res = resolve_entry_reachable(modules, ["index.ts"])
    assert res.poison_all is False
    assert ("index.ts", "top") in res.reachable
    assert ("index.ts", "makeThing") in res.reachable  # default IS part of the entry's own surface


def test_unresolved_named_forward_is_uncertain_not_poison() -> None:
    modules = {"index.ts": _mod({"x": Forward("./nope", "srcX")})}
    res = resolve_entry_reachable(modules, ["index.ts"])
    assert res.poison_all is False  # a single external named re-export does not disable narrowing
    assert res.uncertain_names == {"srcX"}  # keyed on the source name (matches the binding)


def test_unresolved_star_poisons_all() -> None:
    modules = {"index.ts": _mod(stars=["./nope"])}
    res = resolve_entry_reachable(modules, ["index.ts"])
    assert res.poison_all is True  # a wildcard could expose anything → cannot narrow


def test_entry_absent_from_modules_poisons_all() -> None:
    res = resolve_entry_reachable({}, ["index.ts"])
    assert res.poison_all is True
    assert res.reachable == set()


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
        app, ["scan", ".", "--typescript", "--json", "--no-auto-cov", "--no-git", *extra]
    )


def _visibility(result: Any) -> dict[str, bool]:
    """{qualname: is_public} for the scored TypeScript functions in a --typescript --json scan."""
    import json

    payload = json.loads(result.stdout)
    return {fn["qualname"]: fn["is_public"] for fn in payload["functions"] if fn["language"] == "typescript"}


def test_barrel_narrowing_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    result = _scan(_isolated_barrel(tmp_path), monkeypatch)
    assert result.exit_code == 0, (result.stdout, result.stderr)
    vis = _visibility(result)
    assert vis["exposed"] is True  # re-exported by name
    assert vis["alsoExposed"] is False  # file-exported, not re-exported → narrowed
    assert vis["helper"] is True  # via `export *`
    assert vis["hidden"] is False  # unreferenced module → narrowed
    assert "narrowed to entry index.ts" in result.stderr  # R4: the driving entry is announced


def test_no_entry_keeps_file_export_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    app_dir = _isolated_barrel(tmp_path)
    (app_dir / "index.ts").unlink()  # remove the only barrel → no entry → no narrowing
    result = _scan(app_dir, monkeypatch)
    assert result.exit_code == 0, (result.stdout, result.stderr)
    vis = _visibility(result)
    assert vis["alsoExposed"] is True  # every file-exported function keeps its public flag
    assert vis["hidden"] is True


def test_unresolved_wildcard_poisons_and_keeps_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # An unresolved `export *` could expose anything → the whole surface can't be bounded.
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    app_dir = _isolated_barrel(tmp_path)
    (app_dir / "index.ts").write_text(
        'export { exposed } from "./public_api";\nexport * from "external-pkg";\n',
        encoding="utf-8",
    )
    result = _scan(app_dir, monkeypatch)
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert "surface can't be bounded" in result.stderr
    assert _visibility(result)["alsoExposed"] is True  # not demoted on an unproven graph


def test_external_named_reexport_still_narrows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # R2: a single external *named* re-export must NOT disable narrowing (the common case).
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    app_dir = _isolated_barrel(tmp_path)
    (app_dir / "index.ts").write_text(
        'export { exposed } from "./public_api";\nexport { useState } from "react";\n',
        encoding="utf-8",
    )
    result = _scan(app_dir, monkeypatch)
    assert result.exit_code == 0, (result.stdout, result.stderr)
    vis = _visibility(result)
    assert vis["exposed"] is True
    assert vis["alsoExposed"] is False  # still narrowed despite the external re-export
    assert vis["hidden"] is False


def test_uncertain_name_behind_alias_is_kept_public(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A function whose binding matches an unresolved *named* re-export's source name is held
    # public (it might be alias-exposed), while an unrelated sibling still narrows.
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    app_dir = tmp_path / "pkg"
    app_dir.mkdir()
    (app_dir / "index.ts").write_text('export { helper } from "@app/core";\n', encoding="utf-8")
    (app_dir / "helpers.ts").write_text("export function helper() { return 1; }\n", encoding="utf-8")
    (app_dir / "other.ts").write_text("export function unrelated() { return 2; }\n", encoding="utf-8")
    result = _scan(app_dir, monkeypatch)
    assert result.exit_code == 0, (result.stdout, result.stderr)
    vis = _visibility(result)
    assert vis["helper"] is True  # binding matches the uncertain source name
    assert vis["unrelated"] is False  # unrelated → narrowed


def test_partial_unmatched_ts_entry_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    app_dir = _isolated_barrel(tmp_path)
    (app_dir / "index.ts").unlink()
    result = _scan(app_dir, monkeypatch, "--ts-entry", "public_api.ts", "--ts-entry", "nope.ts")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert "1 --ts-entry path(s) matched no scanned file" in result.stderr
    assert _visibility(result)["exposed"] is True  # narrowing still runs on the matched entry


def test_explicit_ts_entry_overrides_detection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    app_dir = _isolated_barrel(tmp_path)
    (app_dir / "index.ts").unlink()  # no auto entry; point --ts-entry at public_api.ts
    result = _scan(app_dir, monkeypatch, "--ts-entry", "public_api.ts")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    # public_api.ts is the entry, so both its exports are public; helpers/internal narrow.
    vis = _visibility(result)
    assert vis["exposed"] is True
    assert vis["alsoExposed"] is True
    assert vis["helper"] is False
    assert vis["hidden"] is False


# --- 0.3.6: a generated file is valid TypeScript; an entry that did not parse refuses ---
#
# `analyze_ts_file` returned an empty module for a generated file, so a barrel's
# `export * from './generated'` resolved to nothing — and a *generated entry* (a
# barrel written by a tool) demoted every function in the package to internal behind
# the ordinary "narrowed" line. A syntax-broken entry did the same. Now a generated
# file keeps its export surface (only its functions are dropped), and only a resolved
# entry that failed to parse refuses to narrow.

_LIB = "export function tsRisky(a: number): number { return a; }\nexport function tsHidden(): number { return 2; }\n"


def _package(tmp_path: Path, index: str, *, generated_module: str | None = None) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "lib.ts").write_text(_LIB, encoding="utf-8")
    (pkg / "index.ts").write_text(index, encoding="utf-8")
    if generated_module is not None:
        (pkg / "generated.ts").write_text(generated_module, encoding="utf-8")
    return pkg


def test_a_barrel_that_re_exports_a_generated_module_narrows_exactly_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    generated = "// @generated by codegen\nexport function genFn(): number { return 1; }\n"
    index = "export { tsRisky } from './lib';\nexport * from './generated';\n"
    with_marker = _scan(_package(tmp_path / "a", index, generated_module=generated), monkeypatch)
    without_marker = _scan(
        _package(tmp_path / "b", index, generated_module=generated.replace("// @generated by codegen\n", "")),
        monkeypatch,
    )

    assert with_marker.exit_code == 0, with_marker.output
    assert _visibility(with_marker) == {"tsRisky": True, "tsHidden": False}
    # Dropping the marker adds `genFn` to the scored set and changes nothing else.
    assert _visibility(without_marker) == {"tsRisky": True, "tsHidden": False, "genFn": True}
    assert "can't be bounded" not in with_marker.output


def test_a_generated_entry_barrel_still_narrows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    pkg = _package(tmp_path, "// @generated by barrelgen\nexport { tsRisky } from './lib';\n")

    result = _scan(pkg, monkeypatch)

    assert result.exit_code == 0, result.output
    assert _visibility(result) == {"tsRisky": True, "tsHidden": False}
    import json

    assert json.loads(result.stdout)["summary"]["skipped_generated_files"] == 1


def test_an_entry_that_did_not_parse_keeps_every_export_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    pkg = _package(tmp_path, "export { tsRisky } from './lib'\nexport {\n")  # unterminated

    result = _scan(pkg, monkeypatch)

    assert result.exit_code == 0, result.output
    assert _visibility(result) == {"tsRisky": True, "tsHidden": True}
    assert "entry index.ts did not parse" in result.output
    assert "can't be bounded" in result.output
    assert "narrowed to entry" not in result.output


def test_a_transitively_broken_module_keeps_the_empty_module_behaviour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only a *resolved entry* refuses. A file the barrel merely re-exports that failed to
    parse contributes nothing, exactly as before — the surface reachable through it is empty."""
    pytest.importorskip("tree_sitter")
    pytest.importorskip("tree_sitter_typescript")
    pkg = _package(
        tmp_path,
        "export { tsRisky } from './lib';\nexport * from './generated';\n",
        generated_module="export {\n",
    )

    result = _scan(pkg, monkeypatch)

    assert result.exit_code == 0, result.output
    assert _visibility(result) == {"tsRisky": True, "tsHidden": False}
    assert "narrowed to entry index.ts" in result.output
