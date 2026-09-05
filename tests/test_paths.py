"""`_paths.relative_posix`: one key per file (0.3.6)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from riskratchet._paths import relative_posix


def test_inside_the_root_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.py").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert relative_posix(Path("src/x.py"), tmp_path) == "src/x.py"
    assert relative_posix(tmp_path / "src" / "x.py", tmp_path) == "src/x.py"


def test_outside_the_root_is_one_dotdot_key_from_any_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    other = tmp_path / "other" / "src"
    other.mkdir(parents=True)
    (other / "app.py").write_text("", encoding="utf-8")

    monkeypatch.chdir(root)
    assert relative_posix(Path("../other/src/app.py"), root) == "../other/src/app.py"
    assert relative_posix(other / "app.py", root) == "../other/src/app.py"
    monkeypatch.chdir(root / "sub")
    assert relative_posix(Path("../../other/src/app.py"), root) == "../other/src/app.py"


def test_a_symlinked_root_keeps_the_spelling_it_was_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = tmp_path / "real_src"
    real.mkdir()
    (real / "x.py").write_text("", encoding="utf-8")
    try:
        os.symlink(real, tmp_path / "src", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    monkeypatch.chdir(tmp_path)

    assert relative_posix(Path("src/x.py"), tmp_path) == "src/x.py"
    assert relative_posix(tmp_path / "src" / "x.py", tmp_path) == "src/x.py"


def test_a_path_with_no_relative_form_keeps_todays_behaviour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows, different drives: `os.path.relpath` raises; fall back to the path as given."""
    other = tmp_path / "elsewhere" / "app.py"
    other.parent.mkdir()
    other.write_text("", encoding="utf-8")
    root = tmp_path / "proj"
    root.mkdir()

    def cross_drive(*_args: object, **_kwargs: object) -> str:
        raise ValueError("path is on mount 'D:', start on mount 'C:'")

    monkeypatch.setattr(os.path, "relpath", cross_drive)
    monkeypatch.chdir(root)

    assert relative_posix(other, root) == other.as_posix()
