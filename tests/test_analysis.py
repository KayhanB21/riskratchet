"""Tests for AST-based function discovery and public-surface detection."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from riskratchet.analysis import (
    ParseError,
    is_public_qualname,
    iter_python_files,
    parse_file,
)


def _write(tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / name
    path.write_text(dedent(source).strip() + "\n", encoding="utf-8")
    return path


def test_discovers_module_level_functions(tmp_path: Path) -> None:
    path = _write(tmp_path, "m.py", """
        def foo():
            return 1

        def bar(x):
            return x + 1
    """)
    parsed = parse_file(path, root=tmp_path)
    assert not isinstance(parsed, ParseError)
    names = [fn.id.qualname for fn in parsed.functions]
    assert names == ["foo", "bar"]


def test_discovers_class_methods_with_qualified_names(tmp_path: Path) -> None:
    path = _write(tmp_path, "m.py", """
        class Foo:
            def bar(self):
                return 1

            def _internal(self):
                return 2
    """)
    parsed = parse_file(path, root=tmp_path)
    assert not isinstance(parsed, ParseError)
    qualnames = sorted(fn.id.qualname for fn in parsed.functions)
    assert qualnames == ["Foo._internal", "Foo.bar"]


def test_discovers_nested_functions(tmp_path: Path) -> None:
    path = _write(tmp_path, "m.py", """
        def outer():
            def inner():
                return 1
            return inner()
    """)
    parsed = parse_file(path, root=tmp_path)
    assert not isinstance(parsed, ParseError)
    qualnames = sorted(fn.id.qualname for fn in parsed.functions)
    assert qualnames == ["outer", "outer.inner"]


def test_discovers_async_functions(tmp_path: Path) -> None:
    path = _write(tmp_path, "m.py", """
        async def fetch():
            return 1
    """)
    parsed = parse_file(path, root=tmp_path)
    assert not isinstance(parsed, ParseError)
    assert parsed.functions[0].is_async is True


def test_decorators_do_not_change_qualname(tmp_path: Path) -> None:
    path = _write(tmp_path, "m.py", """
        def deco(fn):
            return fn

        @deco
        def wrapped():
            return 1
    """)
    parsed = parse_file(path, root=tmp_path)
    assert not isinstance(parsed, ParseError)
    qualnames = sorted(fn.id.qualname for fn in parsed.functions)
    assert qualnames == ["deco", "wrapped"]


def test_is_public_qualname_handles_underscores_and_dunders() -> None:
    assert is_public_qualname("foo") is True
    assert is_public_qualname("_foo") is False
    assert is_public_qualname("Foo.bar") is True
    assert is_public_qualname("Foo._bar") is False
    assert is_public_qualname("Foo.__init__") is True
    assert is_public_qualname("_Foo.bar") is False


def test_parse_error_returned_for_syntax_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.py"
    path.write_text("def broken( :\n    pass\n", encoding="utf-8")
    result = parse_file(path, root=tmp_path)
    assert isinstance(result, ParseError)
    assert "syntax error" in result.message


def test_file_stats_count_lines_and_functions(tmp_path: Path) -> None:
    path = _write(tmp_path, "m.py", """
        def a():
            return 1

        def b():
            return 2
    """)
    parsed = parse_file(path, root=tmp_path)
    assert not isinstance(parsed, ParseError)
    assert parsed.file_stats.function_count == 2
    assert parsed.file_stats.total_lines > 0


def test_iter_python_files_filters_by_glob(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src" / "test_a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src" / "subdir").mkdir()
    (tmp_path / "src" / "subdir" / "b.py").write_text("x = 1\n", encoding="utf-8")

    all_files = iter_python_files([tmp_path / "src"], root=tmp_path)
    rel = sorted(p.relative_to(tmp_path).as_posix() for p in all_files)
    assert rel == ["src/a.py", "src/subdir/b.py", "src/test_a.py"]

    excluded = iter_python_files(
        [tmp_path / "src"],
        root=tmp_path,
        exclude=["src/test_*.py"],
    )
    rel = sorted(p.relative_to(tmp_path).as_posix() for p in excluded)
    assert "src/test_a.py" not in rel
    assert "src/a.py" in rel


def test_iter_python_files_skips_hidden_directories(tmp_path: Path) -> None:
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "lib.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
    files = iter_python_files([tmp_path], root=tmp_path)
    rel = sorted(p.relative_to(tmp_path).as_posix() for p in files)
    assert rel == ["src.py"]


def test_staticmethod_and_classmethod_keep_class_qualname(tmp_path: Path) -> None:
    path = _write(tmp_path, "m.py", """
        class Foo:
            @staticmethod
            def static_one():
                return 1

            @classmethod
            def class_one(cls):
                return 2
    """)
    parsed = parse_file(path, root=tmp_path)
    assert not isinstance(parsed, ParseError)
    qualnames = sorted(fn.id.qualname for fn in parsed.functions)
    assert qualnames == ["Foo.class_one", "Foo.static_one"]


def test_multiline_signature_spans_whole_definition(tmp_path: Path) -> None:
    path = _write(tmp_path, "m.py", """
        def wide(
            a: int,
            b: int,
            c: int,
        ) -> int:
            return a + b + c
    """)
    parsed = parse_file(path, root=tmp_path)
    assert not isinstance(parsed, ParseError)
    fn = parsed.functions[0]
    # The function starts on the `def` line and ends at `return`, so the span
    # has to cover the whole signature plus the body.
    assert fn.span.start_line == 1
    assert fn.span.end_line >= 6


def test_parse_error_returned_for_non_utf8_file(tmp_path: Path) -> None:
    path = tmp_path / "binary.py"
    path.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00")
    result = parse_file(path, root=tmp_path)
    assert isinstance(result, ParseError)
    assert "cannot read file" in result.message or "syntax error" in result.message
