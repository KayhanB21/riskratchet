"""Tests for coverage.json parsing and per-span mapping."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from riskratchet.coverage import coverage_for_span, empty_coverage, load_coverage
from riskratchet.models import FunctionSpan


def _write(tmp_path: Path, payload: dict) -> Path:
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
    file_cov = {"executed_lines": [], "missing_lines": []}
    stats = coverage_for_span(file_cov, FunctionSpan(1, 10))
    assert stats.line_coverage == 1.0


def test_coverage_for_span_partial_lines() -> None:
    file_cov = {
        "executed_lines": [2, 3],
        "missing_lines": [4, 5],
    }
    stats = coverage_for_span(file_cov, FunctionSpan(1, 10))
    assert stats.line_coverage == pytest.approx(0.5)
    assert stats.missing_lines == (4, 5)


def test_coverage_for_span_uses_branch_data() -> None:
    file_cov = {
        "executed_lines": [2],
        "missing_lines": [],
        "executed_branches": [[2, 3]],
        "missing_branches": [[2, 5]],
    }
    stats = coverage_for_span(file_cov, FunctionSpan(1, 10))
    assert stats.branch_coverage == pytest.approx(0.5)
    assert stats.missing_branches == ((2, 5),)


def test_coverage_for_span_no_branch_section_returns_none() -> None:
    file_cov = {"executed_lines": [2], "missing_lines": [3]}
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
