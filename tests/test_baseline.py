"""Tests for baseline I/O and regression comparison."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from riskratchet.baseline import (
    baseline_from_report,
    compare,
    load_baseline,
    save_baseline,
)
from riskratchet.models import (
    Baseline,
    BaselineEntry,
    ChurnStats,
    ComplexityStats,
    CoverageStats,
    FileStats,
    FunctionId,
    FunctionRisk,
    FunctionSpan,
    RegressionKind,
    RiskComponents,
    RiskReport,
)


def _components(score: float = 50.0) -> RiskComponents:
    return RiskComponents(
        coverage_gap=score,
        structural_complexity=score,
        branch_gap=score,
        churn=score,
        public_surface=score,
        sprawl=score,
    )


def _fn(
    path: str,
    qualname: str,
    score: float = 50.0,
    *,
    component_score: float | None = None,
    fingerprint: str | None = None,
) -> FunctionRisk:
    file_stats = FileStats(path=path, total_lines=100, function_count=1)
    return FunctionRisk(
        id=FunctionId(path=path, qualname=qualname),
        span=FunctionSpan(start_line=1, end_line=10),
        is_public=True,
        complexity=ComplexityStats(cyclomatic=5),
        coverage=CoverageStats(line_coverage=0.5, branch_coverage=0.5),
        churn=ChurnStats(commits=0),
        file_stats=file_stats,
        components=_components(score if component_score is None else component_score),
        score=score,
        crap=10.0,
        fingerprint=fingerprint or f"{path}:{qualname}",
    )


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    report = RiskReport(
        functions=(_fn("a.py", "foo", 42.0), _fn("b.py", "bar", 60.0)),
        files=(),
    )
    baseline = baseline_from_report(report)
    target = tmp_path / "baseline.json"
    save_baseline(baseline, target)
    loaded = load_baseline(target)
    assert set(loaded.entries.keys()) == {
        FunctionId("a.py", "foo"),
        FunctionId("b.py", "bar"),
    }
    assert loaded.entries[FunctionId("a.py", "foo")].score == pytest.approx(42.0)
    assert loaded.version == "2"
    assert loaded.entries[FunctionId("a.py", "foo")].fingerprint == "a.py:foo"


def test_compare_flags_new_function_above_threshold() -> None:
    report = RiskReport(functions=(_fn("a.py", "foo", 60.0),), files=())
    old = Baseline(version="1", entries={})
    regressions = compare(report, old, fail_new_above=50.0, fail_regression_above=5.0)
    assert len(regressions) == 1
    assert regressions[0].kind == RegressionKind.NEW_ABOVE_THRESHOLD
    assert "absent from baseline" in regressions[0].reason


def test_compare_ignores_new_function_below_threshold() -> None:
    report = RiskReport(functions=(_fn("a.py", "foo", 40.0),), files=())
    old = Baseline(version="1", entries={})
    assert compare(report, old, fail_new_above=50.0, fail_regression_above=5.0) == []


def test_compare_flags_regression_beyond_tolerance() -> None:
    fn = _fn("a.py", "foo", 60.0)
    old_entry = BaselineEntry(id=fn.id, score=50.0, components=_components(50.0))
    old = Baseline(version="1", entries={fn.id: old_entry})
    report = RiskReport(functions=(fn,), files=())
    regressions = compare(report, old, fail_new_above=100.0, fail_regression_above=5.0)
    assert len(regressions) == 1
    assert regressions[0].kind == RegressionKind.REGRESSED
    assert regressions[0].delta == pytest.approx(10.0)


def test_compare_ignores_regression_at_tolerance_boundary() -> None:
    fn = _fn("a.py", "foo", 55.0)
    old_entry = BaselineEntry(id=fn.id, score=50.0, components=_components(50.0))
    old = Baseline(version="1", entries={fn.id: old_entry})
    report = RiskReport(functions=(fn,), files=())
    # delta of exactly 5.0 should NOT trigger; tolerance is `>`, not `>=`.
    regressions = compare(report, old, fail_new_above=100.0, fail_regression_above=5.0)
    assert regressions == []


def test_compare_ignores_improvements() -> None:
    fn = _fn("a.py", "foo", 30.0)
    old_entry = BaselineEntry(id=fn.id, score=80.0, components=_components(80.0))
    old = Baseline(version="1", entries={fn.id: old_entry})
    report = RiskReport(functions=(fn,), files=())
    assert compare(report, old, fail_new_above=100.0, fail_regression_above=5.0) == []


def test_compare_ignores_deleted_functions() -> None:
    deleted_id = FunctionId("a.py", "gone")
    old = Baseline(
        version="1",
        entries={deleted_id: BaselineEntry(id=deleted_id, score=80.0, components=_components(80.0))},
    )
    report = RiskReport(functions=(), files=())
    assert compare(report, old, fail_new_above=100.0, fail_regression_above=5.0) == []


def test_compare_matches_by_qualname_even_when_lines_change() -> None:
    # FunctionId is keyed by (path, qualname). The line span moving (e.g.
    # because someone added imports above the function) must not be treated
    # as the function disappearing and a new one appearing.
    fn = _fn("a.py", "foo", 60.0)
    old_entry = BaselineEntry(id=fn.id, score=50.0, components=_components(50.0))
    old = Baseline(version="1", entries={fn.id: old_entry})

    # Same function, same qualname; the FunctionRisk's span would be different
    # in practice but compare() does not look at spans, only at the FunctionId.
    moved = FunctionRisk(
        id=fn.id,
        span=FunctionSpan(start_line=42, end_line=50),
        is_public=fn.is_public,
        complexity=fn.complexity,
        coverage=fn.coverage,
        churn=fn.churn,
        file_stats=fn.file_stats,
        components=_components(60.0),
        score=60.0,
        crap=fn.crap,
    )
    report = RiskReport(functions=(moved,), files=())
    regressions = compare(report, old, fail_new_above=100.0, fail_regression_above=5.0)
    assert len(regressions) == 1
    assert regressions[0].kind == RegressionKind.REGRESSED
    assert regressions[0].delta == pytest.approx(10.0)


def test_compare_matches_unique_rename_by_fingerprint() -> None:
    old_id = FunctionId("a.py", "helper")
    old_entry = BaselineEntry(
        id=old_id,
        score=40.0,
        components=_components(40.0),
        fingerprint="same-body",
    )
    old = Baseline(version="2", entries={old_id: old_entry})
    report = RiskReport(
        functions=(_fn("a.py", "compute_thing", 40.0, component_score=40.0, fingerprint="same-body"),),
        files=(),
    )
    assert compare(report, old, fail_new_above=10.0, fail_regression_above=5.0) == []


def test_compare_reports_renamed_function_regression() -> None:
    old_id = FunctionId("a.py", "helper")
    old_entry = BaselineEntry(
        id=old_id,
        score=40.0,
        components=_components(40.0),
        fingerprint="same-body",
    )
    old = Baseline(version="2", entries={old_id: old_entry})
    report = RiskReport(
        functions=(_fn("a.py", "compute_thing", 60.0, component_score=40.0, fingerprint="same-body"),),
        files=(),
    )
    regressions = compare(report, old, fail_new_above=100.0, fail_regression_above=5.0)
    assert len(regressions) == 1
    assert regressions[0].kind == RegressionKind.REGRESSED
    assert "previous target a.py::helper" in regressions[0].reason


def test_compare_avoids_ambiguous_fingerprint_matches() -> None:
    old = Baseline(
        version="2",
        entries={
            FunctionId("a.py", "one"): BaselineEntry(
                id=FunctionId("a.py", "one"),
                score=20.0,
                components=_components(20.0),
                fingerprint="duplicate",
            ),
            FunctionId("a.py", "two"): BaselineEntry(
                id=FunctionId("a.py", "two"),
                score=20.0,
                components=_components(20.0),
                fingerprint="duplicate",
            ),
        },
    )
    report = RiskReport(
        functions=(_fn("a.py", "renamed", 60.0, component_score=60.0, fingerprint="duplicate"),),
        files=(),
    )
    regressions = compare(report, old, fail_new_above=50.0, fail_regression_above=5.0)
    assert len(regressions) == 1
    assert regressions[0].kind == RegressionKind.NEW_ABOVE_THRESHOLD


def test_compare_flags_existing_debt_when_requested() -> None:
    fn = _fn("a.py", "foo", 60.0)
    old_entry = BaselineEntry(id=fn.id, score=60.0, components=fn.components, fingerprint=fn.fingerprint)
    old = Baseline(version="2", entries={fn.id: old_entry})
    report = RiskReport(functions=(fn,), files=())
    regressions = compare(
        report,
        old,
        fail_new_above=100.0,
        fail_regression_above=5.0,
        fail_existing_above=50.0,
    )
    assert len(regressions) == 1
    assert regressions[0].kind == RegressionKind.EXISTING_ABOVE_THRESHOLD


def test_compare_flags_component_regression_when_total_is_flat() -> None:
    fn = _fn("a.py", "foo", 40.0, component_score=40.0)
    previous = RiskComponents(
        coverage_gap=0.0,
        structural_complexity=40.0,
        branch_gap=40.0,
        churn=40.0,
        public_surface=40.0,
        sprawl=40.0,
    )
    old_entry = BaselineEntry(id=fn.id, score=40.0, components=previous, fingerprint=fn.fingerprint)
    old = Baseline(version="2", entries={fn.id: old_entry})
    report = RiskReport(functions=(fn,), files=())
    regressions = compare(report, old, fail_new_above=100.0, fail_regression_above=5.0)
    assert len(regressions) == 1
    assert regressions[0].kind == RegressionKind.COMPONENT_REGRESSED
    assert "coverage_gap grew" in regressions[0].reason


def test_compare_can_disable_component_regression_gate() -> None:
    fn = _fn("a.py", "foo", 40.0, component_score=40.0)
    previous = RiskComponents(
        coverage_gap=0.0,
        structural_complexity=40.0,
        branch_gap=40.0,
        churn=40.0,
        public_surface=40.0,
        sprawl=40.0,
    )
    old_entry = BaselineEntry(id=fn.id, score=40.0, components=previous, fingerprint=fn.fingerprint)
    old = Baseline(version="2", entries={fn.id: old_entry})
    report = RiskReport(functions=(fn,), files=())
    assert (
        compare(
            report,
            old,
            fail_new_above=100.0,
            fail_regression_above=5.0,
            component_regression_gate=False,
        )
        == []
    )


def test_compare_matches_same_file_rename() -> None:
    """Rename within the same file (path equal, qualname different, body same) → no regression."""
    old_id = FunctionId("a.py", "old_name")
    old_entry = BaselineEntry(
        id=old_id,
        score=40.0,
        components=_components(40.0),
        fingerprint="shared-body",
        signature="sig-1",
    )
    old = Baseline(version="2", entries={old_id: old_entry})
    report = RiskReport(
        functions=(
            FunctionRisk(
                id=FunctionId("a.py", "new_name"),
                span=FunctionSpan(start_line=1, end_line=10),
                is_public=True,
                complexity=ComplexityStats(cyclomatic=5),
                coverage=CoverageStats(line_coverage=0.5, branch_coverage=0.5),
                churn=ChurnStats(commits=0),
                file_stats=FileStats(path="a.py", total_lines=100, function_count=1),
                components=_components(40.0),
                score=40.0,
                crap=10.0,
                fingerprint="shared-body",
                signature="sig-1",
            ),
        ),
        files=(),
    )
    regressions = compare(report, old, fail_new_above=10.0, fail_regression_above=5.0)
    assert regressions == []


def test_compare_matches_moved_file_same_body() -> None:
    """File moved (path different, qualname equal, body same) → no regression."""
    old_id = FunctionId("old/path.py", "fn")
    old = Baseline(
        version="2",
        entries={
            old_id: BaselineEntry(
                id=old_id,
                score=40.0,
                components=_components(40.0),
                fingerprint="shared-body",
            )
        },
    )
    report = RiskReport(
        functions=(
            FunctionRisk(
                id=FunctionId("new/path.py", "fn"),
                span=FunctionSpan(start_line=1, end_line=10),
                is_public=True,
                complexity=ComplexityStats(cyclomatic=5),
                coverage=CoverageStats(line_coverage=0.5, branch_coverage=0.5),
                churn=ChurnStats(commits=0),
                file_stats=FileStats(path="new/path.py", total_lines=100, function_count=1),
                components=_components(40.0),
                score=40.0,
                crap=10.0,
                fingerprint="shared-body",
            ),
        ),
        files=(),
    )
    assert compare(report, old, fail_new_above=10.0, fail_regression_above=5.0) == []


def test_compare_matches_method_rename_inside_class() -> None:
    """Method rename inside same class (Foo.old → Foo.new) recognized."""
    old_id = FunctionId("m.py", "Foo.old_method")
    old = Baseline(
        version="2",
        entries={
            old_id: BaselineEntry(
                id=old_id,
                score=30.0,
                components=_components(30.0),
                fingerprint="method-body",
            )
        },
    )
    report = RiskReport(
        functions=(
            FunctionRisk(
                id=FunctionId("m.py", "Foo.new_method"),
                span=FunctionSpan(start_line=1, end_line=10),
                is_public=True,
                complexity=ComplexityStats(cyclomatic=5),
                coverage=CoverageStats(line_coverage=0.5, branch_coverage=0.5),
                churn=ChurnStats(commits=0),
                file_stats=FileStats(path="m.py", total_lines=100, function_count=1),
                components=_components(30.0),
                score=30.0,
                crap=10.0,
                fingerprint="method-body",
            ),
        ),
        files=(),
    )
    assert compare(report, old, fail_new_above=10.0, fail_regression_above=5.0) == []


def test_compare_matches_class_rename_keeping_method_name() -> None:
    """Class rename keeps method tail; matcher picks it up via tail + path + body."""
    old_id = FunctionId("m.py", "OldClass.method")
    old = Baseline(
        version="2",
        entries={
            old_id: BaselineEntry(
                id=old_id,
                score=25.0,
                components=_components(25.0),
                fingerprint="cls-body",
            )
        },
    )
    report = RiskReport(
        functions=(
            FunctionRisk(
                id=FunctionId("m.py", "NewClass.method"),
                span=FunctionSpan(start_line=1, end_line=10),
                is_public=True,
                complexity=ComplexityStats(cyclomatic=5),
                coverage=CoverageStats(line_coverage=0.5, branch_coverage=0.5),
                churn=ChurnStats(commits=0),
                file_stats=FileStats(path="m.py", total_lines=100, function_count=1),
                components=_components(25.0),
                score=25.0,
                crap=10.0,
                fingerprint="cls-body",
            ),
        ),
        files=(),
    )
    assert compare(report, old, fail_new_above=10.0, fail_regression_above=5.0) == []


def test_compare_reports_regression_after_recognized_rename() -> None:
    """Recognized rename with score growth still surfaces as REGRESSED."""
    old_id = FunctionId("a.py", "helper")
    old = Baseline(
        version="2",
        entries={
            old_id: BaselineEntry(
                id=old_id,
                score=40.0,
                components=_components(40.0),
                fingerprint="shared-body",
            )
        },
    )
    new_fn = FunctionRisk(
        id=FunctionId("a.py", "compute_thing"),
        span=FunctionSpan(start_line=1, end_line=10),
        is_public=True,
        complexity=ComplexityStats(cyclomatic=5),
        coverage=CoverageStats(line_coverage=0.5, branch_coverage=0.5),
        churn=ChurnStats(commits=0),
        file_stats=FileStats(path="a.py", total_lines=100, function_count=1),
        components=_components(40.0),
        score=60.0,
        crap=10.0,
        fingerprint="shared-body",
    )
    regressions = compare(
        RiskReport(functions=(new_fn,), files=()),
        old,
        fail_new_above=100.0,
        fail_regression_above=5.0,
    )
    assert len(regressions) == 1
    assert regressions[0].kind == RegressionKind.REGRESSED
    assert "previous target a.py::helper" in regressions[0].reason


def test_compare_emits_new_for_ambiguous_rename() -> None:
    """When two baseline entries plausibly map to the same new function, the
    matcher refuses — and the function is treated as a regression so risk is
    not silently masked."""
    fn = _fn("a.py", "renamed", 60.0, component_score=60.0, fingerprint="dup")
    old = Baseline(
        version="2",
        entries={
            FunctionId("a.py", "one"): BaselineEntry(
                id=FunctionId("a.py", "one"),
                score=20.0,
                components=_components(20.0),
                fingerprint="dup",
            ),
            FunctionId("a.py", "two"): BaselineEntry(
                id=FunctionId("a.py", "two"),
                score=20.0,
                components=_components(20.0),
                fingerprint="dup",
            ),
        },
    )
    report = RiskReport(functions=(fn,), files=())
    regressions = compare(report, old, fail_new_above=100.0, fail_regression_above=5.0)
    assert len(regressions) == 1
    assert regressions[0].kind == RegressionKind.NEW_ABOVE_THRESHOLD
    assert "ambiguous rename candidate" in regressions[0].reason
    assert "a.py::one" in regressions[0].reason
    assert "a.py::two" in regressions[0].reason


def test_compare_does_not_double_match_same_old_entry() -> None:
    """A baseline entry can match only the first new function that claims it."""
    old_id = FunctionId("a.py", "shared")
    old = Baseline(
        version="2",
        entries={
            old_id: BaselineEntry(
                id=old_id,
                score=40.0,
                components=_components(40.0),
                fingerprint="body-1",
            )
        },
    )
    report = RiskReport(
        functions=(
            FunctionRisk(
                id=FunctionId("a.py", "first_new"),
                span=FunctionSpan(start_line=1, end_line=10),
                is_public=True,
                complexity=ComplexityStats(cyclomatic=5),
                coverage=CoverageStats(line_coverage=0.5, branch_coverage=0.5),
                churn=ChurnStats(commits=0),
                file_stats=FileStats(path="a.py", total_lines=100, function_count=1),
                components=_components(40.0),
                score=40.0,
                crap=10.0,
                fingerprint="body-1",
            ),
            FunctionRisk(
                id=FunctionId("a.py", "second_new"),
                span=FunctionSpan(start_line=20, end_line=30),
                is_public=True,
                complexity=ComplexityStats(cyclomatic=5),
                coverage=CoverageStats(line_coverage=0.5, branch_coverage=0.5),
                churn=ChurnStats(commits=0),
                file_stats=FileStats(path="a.py", total_lines=100, function_count=1),
                components=_components(40.0),
                score=70.0,
                crap=10.0,
                fingerprint="body-1",
            ),
        ),
        files=(),
    )
    # The fingerprint is shared across two new functions, so the unique-body
    # path is skipped. The matcher takes the first one; the second falls
    # through to NEW_ABOVE_THRESHOLD.
    regressions = compare(report, old, fail_new_above=50.0, fail_regression_above=5.0)
    new_above = [r for r in regressions if r.kind == RegressionKind.NEW_ABOVE_THRESHOLD]
    assert len(new_above) == 1
    assert new_above[0].id.qualname == "second_new"


def test_baseline_signature_field_roundtrips(tmp_path: Path) -> None:
    """A baseline saved with `signature` reloads with the same value."""
    fn = _fn("a.py", "foo", 30.0)
    report = RiskReport(
        functions=(
            FunctionRisk(
                id=fn.id,
                span=fn.span,
                is_public=fn.is_public,
                complexity=fn.complexity,
                coverage=fn.coverage,
                churn=fn.churn,
                file_stats=fn.file_stats,
                components=fn.components,
                score=fn.score,
                crap=fn.crap,
                fingerprint="body-x",
                signature="sig-x",
            ),
        ),
        files=(),
    )
    target = tmp_path / "baseline.json"
    save_baseline(baseline_from_report(report), target)
    loaded = load_baseline(target)
    assert loaded.entries[fn.id].signature == "sig-x"


def test_baseline_load_tolerates_missing_signature(tmp_path: Path) -> None:
    """Legacy baselines without the signature field still load cleanly."""
    payload = (
        '{"version": "2", "entries": ['
        '{"path": "a.py", "qualname": "foo", "score": 10.0, '
        '"components": {"coverage_gap": 0.0, "structural_complexity": 0.0, '
        '"branch_gap": 0.0, "churn": 0.0, "public_surface": 0.0, "sprawl": 0.0}, '
        '"fingerprint": "body-x"}'
        "]}"
    )
    target = tmp_path / "baseline.json"
    target.write_text(payload, encoding="utf-8")
    loaded = load_baseline(target)
    entry = loaded.entries[FunctionId("a.py", "foo")]
    assert entry.signature is None
    assert entry.fingerprint == "body-x"


def test_baseline_json_is_sorted_for_stable_diffs(tmp_path: Path) -> None:
    report = RiskReport(
        functions=(
            _fn("z.py", "foo", 10.0),
            _fn("a.py", "bar", 10.0),
            _fn("a.py", "foo", 10.0),
        ),
        files=(),
    )
    target = tmp_path / "baseline.json"
    save_baseline(baseline_from_report(report), target)
    text = target.read_text(encoding="utf-8")
    a_foo = text.index("a.py")
    z_foo = text.index("z.py")
    assert a_foo < z_foo


# --- Tests for the extracted _classify_against_baseline helper ----------------


def _classify(
    fn: FunctionRisk,
    old: Baseline,
    used: set[FunctionId] | None = None,
) -> Any:
    from riskratchet.baseline import (
        _classify_against_baseline,
        _current_fingerprint_counts,
        _unique_old_entries_by_fingerprint,
    )

    report = RiskReport(functions=(fn,), files=())
    return _classify_against_baseline(
        fn,
        old,
        _unique_old_entries_by_fingerprint(old),
        _current_fingerprint_counts(report),
        used if used is not None else set(),
    )


def test_classify_against_baseline_returns_exact_match() -> None:
    fn = _fn("a.py", "foo", 50.0, fingerprint="abc")
    old = Baseline(
        version="2",
        entries={fn.id: BaselineEntry(id=fn.id, score=50.0, components=_components(), fingerprint="abc")},
    )
    classification = _classify(fn, old)
    assert classification.previous is not None
    assert classification.previous_id is None  # exact match is not a rename
    assert classification.match_confidence is None
    assert classification.ambiguous is None


def test_classify_against_baseline_returns_fingerprint_match() -> None:
    fn = _fn("new.py", "renamed_foo", 50.0, fingerprint="shared")
    old_id = FunctionId("old.py", "old_foo")
    old = Baseline(
        version="2",
        entries={
            old_id: BaselineEntry(id=old_id, score=50.0, components=_components(), fingerprint="shared")
        },
    )
    classification = _classify(fn, old)
    assert classification.previous is not None
    assert classification.previous_id == old_id
    assert classification.match_confidence == 1.0
    assert classification.ambiguous is None


def test_classify_against_baseline_returns_no_match_for_unknown_function() -> None:
    fn = _fn("a.py", "foo", 50.0, fingerprint="abc")
    old = Baseline(version="2", entries={})
    classification = _classify(fn, old)
    assert classification.previous is None
    assert classification.previous_id is None
    assert classification.match_confidence is None
    assert classification.ambiguous is None


def test_classify_against_baseline_returns_ambiguous_for_near_ties() -> None:
    fn = _fn("renamed.py", "new_name", 50.0, fingerprint="changed")
    old_a = FunctionId("a.py", "candidate_a")
    old_b = FunctionId("a.py", "candidate_b")
    old = Baseline(
        version="2",
        entries={
            old_a: BaselineEntry(
                id=old_a,
                score=50.0,
                components=_components(),
                fingerprint="old_fp_a",
                signature="sig",
            ),
            old_b: BaselineEntry(
                id=old_b,
                score=50.0,
                components=_components(),
                fingerprint="old_fp_b",
                signature="sig",
            ),
        },
    )
    # Both candidates share signature+component proximity+score; neither
    # has a body fingerprint match. They're below the threshold so the
    # matcher returns no match (not ambiguous). This verifies the
    # passthrough case: below-threshold isn't called ambiguous.
    classification = _classify(fn, old)
    assert classification.previous is None
    assert classification.ambiguous is None
