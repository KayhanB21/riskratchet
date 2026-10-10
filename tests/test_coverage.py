"""Tests for coverage.json parsing and per-span mapping."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:
    from pathlib import Path


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


def test_load_coverage_map_raises_for_a_malformed_shard(tmp_path: Path) -> None:
    """0.3.11: a shard that exists and cannot be parsed is an error, as one `--coverage` file is.

    It used to be skipped through `on_error`, so its prefix scored 0% and the gate exited 1.
    """
    good = _write(
        tmp_path,
        {"files": {"pkg/foo.py": {"executed_lines": [1], "missing_lines": []}}},
    )
    junk = tmp_path / "junk.json"
    junk.write_text("not json", encoding="utf-8")
    errors: list[tuple[Path, str]] = []
    with pytest.raises(ValueError, match="could not read coverage file"):
        load_coverage_map(
            {"pkg": good, "bad": junk},
            on_error=lambda path, message: errors.append((path, message)),
        )
    assert errors == []


def test_load_coverage_map_skips_a_missing_shard(tmp_path: Path) -> None:
    good = _write(
        tmp_path,
        {"files": {"pkg/foo.py": {"executed_lines": [1], "missing_lines": []}}},
    )
    gone = tmp_path / "absent.json"
    errors: list[tuple[Path, str]] = []
    multi = load_coverage_map(
        {"pkg": good, "gone": gone},
        on_error=lambda path, message: errors.append((path, message)),
    )

    assert multi.lookup("pkg/foo.py") is not None
    assert errors == [(gone, "file not found")]


def test_load_coverage_map_without_callback_skips_a_missing_shard_silently(tmp_path: Path) -> None:
    """`on_error` is optional: a missing shard is simply absent."""
    multi = load_coverage_map({"gone": tmp_path / "absent.json"})

    assert multi.lookup("gone/foo.py") is None
    assert multi.prefixes == ()


@pytest.mark.parametrize(
    ("prefix", "path"),
    [
        (".tools/a", ".tools/a/src/m.py"),
        ("./packages/a", "packages/a/src/m.py"),
        ("././packages/a/", "packages/a/src/m.py"),
        (".", "src/m.py"),
    ],
)
def test_a_map_prefix_loses_only_a_leading_dot_slash(tmp_path: Path, prefix: str, path: str) -> None:
    """0.3.11: `.lstrip("./")` strips characters, so `.tools/a` became `tools/a` and matched nothing."""
    shard = _write(tmp_path, {"files": {"src/m.py": {"executed_lines": [1], "missing_lines": []}}})

    multi = load_coverage_map({prefix: shard})

    assert multi.lookup(path) is not None


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


# --- 0.3.5: the same loaders must also reject a structurally wrong payload ------

_STRUCTURAL_REJECTS = {
    # Each loader is given the *other* backend's report: valid JSON, right shape
    # for somebody, wrong shape for this loader.
    "coverage": (load_coverage, '{"a.ts": {"statementMap": {}, "s": {}}}'),
    "istanbul": (load_istanbul_coverage, '{"meta": {}, "files": {"a.py": {}}, "totals": {}}'),
}


@pytest.mark.parametrize("loader", sorted(_STRUCTURAL_REJECTS), ids=sorted(_STRUCTURAL_REJECTS))
def test_a_report_from_the_other_backend_is_rejected_not_silently_empty(tmp_path: Path, loader: str) -> None:
    """A well-formed JSON object of the wrong shape must not load as "measured nothing".

    `load_coverage` already rejected an Istanbul report — no `files` section — but
    `load_istanbul_coverage` accepted a coverage.py report as three files named
    `meta`/`files`/`totals`, matched nothing, and produced a silently useless coverage
    view. Under `missing_coverage = skip` that dropped every function and exited 0.
    Swapping `--coverage` and `--ts-coverage` is the way users reach this.
    """
    load, payload = _STRUCTURAL_REJECTS[loader]
    path = tmp_path / "report.json"
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(ValueError):
        load(path)


def test_an_empty_report_is_still_valid(tmp_path: Path) -> None:
    """`{}` is a real report that measured nothing — the structural guard must not eat it."""
    path = tmp_path / "coverage-final.json"
    path.write_text("{}", encoding="utf-8")

    assert load_istanbul_coverage(path).file_paths == ()


# --- 0.3.10: a shard may key its files relative to its own prefix ------------------------
#
# `pytest --cov` run from `packages/alpha` writes `src/m.py`, not `packages/alpha/src/m.py`.
# The lookup asked each shard for the repository-relative path only, so the per-package
# setup `coverage_map` exists for matched nothing and every function scored as uncovered.

_COVERED = {"executed_lines": [1, 2], "missing_lines": []}
_UNCOVERED = {"executed_lines": [], "missing_lines": [1, 2]}


def _shard(tmp_path: Path, name: str, files: dict[str, object]) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({"files": files}), encoding="utf-8")
    return path


def test_a_shard_keyed_relative_to_its_prefix_matches(tmp_path: Path) -> None:
    alpha = _shard(tmp_path, "a.json", {"src/m.py": _COVERED})
    beta = _shard(tmp_path, "b.json", {"src/m.py": _UNCOVERED})

    multi = load_coverage_map({"packages/alpha": alpha, "packages/beta": beta})

    # Same relative key in both shards: the prefix decides which report answers.
    assert multi.lookup("packages/alpha/src/m.py") == _COVERED
    assert multi.lookup("packages/beta/src/m.py") == _UNCOVERED


def test_the_repository_relative_key_still_wins_over_the_prefix_relative_one(tmp_path: Path) -> None:
    """A report that matched before 0.3.10 must match the same entry now."""
    shard = _shard(tmp_path, "a.json", {"packages/alpha/m.py": _COVERED, "m.py": _UNCOVERED})

    multi = load_coverage_map({"packages/alpha": shard})

    assert multi.lookup("packages/alpha/m.py") == _COVERED


def test_the_prefix_relative_match_is_exact_not_by_basename(tmp_path: Path) -> None:
    """`core.py` must not pick up `vendored/core.py`: the single-file suffix rule would."""
    shard = _shard(tmp_path, "a.json", {"vendored/core.py": _COVERED})

    multi = load_coverage_map({"packages/alpha": shard})

    assert multi.lookup("packages/alpha/core.py") is None
    assert multi.lookup("packages/alpha/vendored/core.py") == _COVERED


def test_a_prefix_relative_miss_keeps_walking_to_a_broader_shard(tmp_path: Path) -> None:
    narrow = _shard(tmp_path, "narrow.json", {"other.py": _UNCOVERED})
    broad = _shard(tmp_path, "broad.json", {"alpha/legacy.py": _COVERED})

    multi = load_coverage_map({"packages": broad, "packages/alpha": narrow})

    assert multi.lookup("packages/alpha/legacy.py") == _COVERED
    assert multi.lookup("packages/alpha/other.py") == _UNCOVERED


def test_a_path_outside_every_prefix_is_not_matched_by_a_relative_key(tmp_path: Path) -> None:
    shard = _shard(tmp_path, "a.json", {"m.py": _COVERED})

    multi = load_coverage_map({"packages/alpha": shard})

    assert multi.lookup("m.py") is None
    assert multi.lookup("packages/beta/m.py") is None


def test_a_catch_all_prefix_has_no_prefix_relative_spelling(tmp_path: Path) -> None:
    """An empty prefix is the repository root, so the two spellings are the same path."""
    shard = _shard(tmp_path, "all.json", {"src/m.py": _COVERED})

    multi = load_coverage_map({"": shard})

    assert multi.lookup("src/m.py") == _COVERED
    assert multi.lookup("src/missing.py") is None
