"""Tests for coverage.json parsing and per-span mapping."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from riskratchet.coverage import (
    MultiCoverageData,
    coverage_for_span,
    empty_coverage,
    load_coverage,
    load_coverage_map,
)
from riskratchet.models import FunctionSpan


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
