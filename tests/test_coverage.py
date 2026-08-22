"""Tests for coverage.json parsing and per-span mapping."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from riskratchet.baseline import load_baseline
from riskratchet.coverage import (
    MultiCoverageData,
    coverage_for_span,
    empty_coverage,
    load_coverage,
    load_coverage_map,
)
from riskratchet.models import FunctionSpan
from riskratchet.typescript_coverage import load_istanbul_coverage


def _write(tmp_path: Path, payload: dict[str, Any]) -> Path:
    path = tmp_path / "coverage.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_coverage_indexes_files(tmp_path: Path) -> None:
    payload = {
        "files": {
            "src/foo.py": {"executed_lines": [1], "missing_lines": []},
            "src/bar.py": {"executed_lines": [], "missing_lines": [1]},
        }
    }
    data = load_coverage(_write(tmp_path, payload))
    assert set(data.file_paths) == {"src/foo.py", "src/bar.py"}


def test_lookup_matches_by_relative_path(tmp_path: Path) -> None:
    data = load_coverage(_write(tmp_path, {"files": {"src/foo.py": {"executed_lines": []}}}))
    assert data.lookup("src/foo.py") is not None
    assert data.lookup("missing/foo.py") is None


def test_lookup_falls_back_to_suffix(tmp_path: Path) -> None:
    data = load_coverage(_write(tmp_path, {"files": {"/abs/repo/src/foo.py": {"executed_lines": []}}}))
    assert data.lookup("src/foo.py") is not None


def test_coverage_for_span_no_data_means_uncovered() -> None:
    stats = coverage_for_span(None, FunctionSpan(1, 10))
    assert stats.line_coverage == 0.0


def test_coverage_for_span_empty_span_is_treated_as_covered() -> None:
    file_cov: dict[str, Any] = {"executed_lines": [], "missing_lines": []}
    stats = coverage_for_span(file_cov, FunctionSpan(1, 10))
    assert stats.line_coverage == 1.0


def test_coverage_for_span_partial_lines() -> None:
    file_cov: dict[str, Any] = {
        "executed_lines": [2, 3],
        "missing_lines": [4, 5],
    }
    stats = coverage_for_span(file_cov, FunctionSpan(1, 10))
    assert stats.line_coverage == pytest.approx(0.5)
    assert stats.missing_lines == (4, 5)


def test_coverage_for_span_uses_branch_data() -> None:
    file_cov: dict[str, Any] = {
        "executed_lines": [2],
        "missing_lines": [],
        "executed_branches": [[2, 3]],
        "missing_branches": [[2, 5]],
    }
    stats = coverage_for_span(file_cov, FunctionSpan(1, 10))
    assert stats.branch_coverage == pytest.approx(0.5)
    assert stats.missing_branches == ((2, 5),)


def test_coverage_for_span_no_branch_section_returns_none() -> None:
    file_cov: dict[str, Any] = {"executed_lines": [2], "missing_lines": [3]}
    stats = coverage_for_span(file_cov, FunctionSpan(1, 10))
    assert stats.branch_coverage is None


def test_load_coverage_invalid_file_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_coverage(bad)


def test_load_coverage_missing_files_section_raises(tmp_path: Path) -> None:
    path = _write(tmp_path, {"totals": {}})
    with pytest.raises(ValueError):
        load_coverage(path)


def test_empty_coverage_returns_no_lookups() -> None:
    data = empty_coverage()
    assert data.lookup("anything") is None
    assert data.file_paths == ()


def test_multi_coverage_data_picks_longest_prefix(tmp_path: Path) -> None:
    alpha = _write(
        tmp_path,
        {"files": {"packages/alpha/core.py": {"executed_lines": [1, 2], "missing_lines": []}}},
    )
    alpha = alpha.rename(tmp_path / "cov-a.json")
    beta = _write(
        tmp_path,
        {"files": {"packages/beta/core.py": {"executed_lines": [], "missing_lines": [1]}}},
    )
    beta = beta.rename(tmp_path / "cov-b.json")
    multi = load_coverage_map({"packages/alpha": alpha, "packages/beta": beta})
    assert multi.lookup("packages/alpha/core.py") == {
        "executed_lines": [1, 2],
        "missing_lines": [],
    }
    assert multi.lookup("packages/beta/core.py") == {
        "executed_lines": [],
        "missing_lines": [1],
    }
    assert multi.lookup("packages/gamma/core.py") is None


def test_multi_coverage_data_longest_prefix_wins(tmp_path: Path) -> None:
    """When two prefixes both match, the longer one wins."""
    broad = _write(
        tmp_path,
        {"files": {"packages/alpha/legacy.py": {"executed_lines": [1], "missing_lines": []}}},
    )
    broad = broad.rename(tmp_path / "broad.json")
    narrow = _write(
        tmp_path,
        {"files": {"packages/alpha/core.py": {"executed_lines": [], "missing_lines": [1]}}},
    )
    narrow = narrow.rename(tmp_path / "narrow.json")
    multi = load_coverage_map({"packages": broad, "packages/alpha": narrow})
    # core.py only exists in the narrow shard
    assert multi.lookup("packages/alpha/core.py") == {
        "executed_lines": [],
        "missing_lines": [1],
    }
    # legacy.py is only in the broad shard but the narrow prefix matches
    # the path too; narrow has no entry for it so we keep walking to broad.
    assert multi.lookup("packages/alpha/legacy.py") == {
        "executed_lines": [1],
        "missing_lines": [],
    }


def test_multi_coverage_data_empty_returns_none() -> None:
    multi = MultiCoverageData.from_map({})
    assert multi.lookup("anything.py") is None
    assert multi.prefixes == ()


def test_multi_coverage_data_normalizes_prefix(tmp_path: Path) -> None:
    cov = _write(
        tmp_path,
        {"files": {"pkg/foo.py": {"executed_lines": [1], "missing_lines": []}}},
    )
    multi = load_coverage_map({"./pkg/": cov})
    # Lookup uses normalized prefix matching
    assert multi.lookup("pkg/foo.py") is not None


def test_load_coverage_map_skips_missing_shard_with_callback(tmp_path: Path) -> None:
    """A missing shard is dropped, not raised.

    `config._ensure_coverage_map_exists` already told the user "treating as no
    coverage" for this case; the loader used to raise `FileNotFoundError`
    anyway, so every `scan --coverage-map` run with one absent shard crashed.
    """
    good = _write(
        tmp_path,
        {"files": {"pkg/foo.py": {"executed_lines": [1], "missing_lines": []}}},
    )
    errors: list[tuple[Path, str]] = []
    multi = load_coverage_map(
        {"pkg": good, "gone": tmp_path / "absent.json"},
        on_error=lambda path, message: errors.append((path, message)),
    )

    assert multi.lookup("pkg/foo.py") is not None
    assert errors == [(tmp_path / "absent.json", "file not found")]


def test_load_coverage_map_skips_unreadable_shard(tmp_path: Path) -> None:
    good = _write(
        tmp_path,
        {"files": {"pkg/foo.py": {"executed_lines": [1], "missing_lines": []}}},
    )
    junk = tmp_path / "junk.json"
    junk.write_text("not json", encoding="utf-8")
    errors: list[tuple[Path, str]] = []
    multi = load_coverage_map(
        {"pkg": good, "bad": junk},
        on_error=lambda path, message: errors.append((path, message)),
    )

    assert multi.lookup("pkg/foo.py") is not None
    assert len(errors) == 1
    assert errors[0][0] == junk
    assert "could not read" in errors[0][1]


def test_load_coverage_map_without_callback_skips_silently(tmp_path: Path) -> None:
    """`on_error` is optional: unusable shards are simply absent."""
    junk = tmp_path / "junk.json"
    junk.write_text("not json", encoding="utf-8")

    multi = load_coverage_map({"bad": junk, "gone": tmp_path / "absent.json"})

    assert multi.lookup("bad/foo.py") is None
    assert multi.prefixes == ()


# --- 0.3.4: every JSON loader that raises must reject a non-object root --------

_RAISING_LOADERS = {
    "coverage": load_coverage,
    "istanbul": load_istanbul_coverage,
    "baseline": load_baseline,
}


@pytest.mark.parametrize("loader", sorted(_RAISING_LOADERS), ids=sorted(_RAISING_LOADERS))
@pytest.mark.parametrize("payload", ["[]", '"nope"', "null", "3"], ids=["list", "str", "null", "int"])
def test_a_non_object_root_is_a_value_error_not_an_attribute_error(
    tmp_path: Path, loader: str, payload: str
) -> None:
    """`coverage.load_coverage` was the one loader missing this guard.

    It went straight to `raw.get("files")`, so a top-level JSON array reached the
    user as a raw `AttributeError` traceback and exit 1 — while
    `load_istanbul_coverage`'s docstring claimed to *mirror* it. Pinning all
    three together is what keeps the claim true.
    """
    path = tmp_path / "payload.json"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(ValueError):
        _RAISING_LOADERS[loader](path)


def test_the_non_object_message_names_the_file_and_what_it_got(tmp_path: Path) -> None:
    path = tmp_path / "coverage.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match=r"coverage\.json must be a JSON object, got list"):
        load_coverage(path)
