"""Baseline v3: language field, identity block, v2 compat, grammar-change guard (B6, 0.3.0)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from riskratchet.baseline import (
    baseline_from_report,
    load_baseline,
    save_baseline,
    suppress_stale_typescript_renames,
    typescript_identity_stale,
)
from riskratchet.baseline.io import BASELINE_VERSION
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
    RiskComponents,
    RiskReport,
)


def _fn(path: str, name: str, language: str, fingerprint: str) -> FunctionRisk:
    z = RiskComponents(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    return FunctionRisk(
        id=FunctionId(path=path, qualname=name),
        span=FunctionSpan(start_line=1, end_line=3),
        is_public=True,
        complexity=ComplexityStats(cyclomatic=1),
        coverage=CoverageStats(line_coverage=1.0, branch_coverage=None),
        churn=ChurnStats(commits=0),
        file_stats=FileStats(path=path, total_lines=3, function_count=1),
        components=z,
        score=10.0,
        crap=1.0,
        fingerprint=fingerprint,
        signature=f"sig:{fingerprint}",
        language=language,
    )


def test_python_only_baseline_has_no_identity_or_language(tmp_path: Path) -> None:
    report = RiskReport(functions=(_fn("a.py", "f", "python", "fp1"),), files=())
    baseline = baseline_from_report(report)
    assert baseline.version == BASELINE_VERSION == "3"
    assert baseline.identity == {}
    out = tmp_path / "b.json"
    save_baseline(baseline, out)
    raw = json.loads(out.read_text(encoding="utf-8"))
    assert "identity" not in raw  # omitted for a Python-only baseline
    assert "language" not in raw["entries"][0]  # omit-when-python keeps v2 byte shape


def test_python_only_v3_differs_from_v2_only_in_version(tmp_path: Path) -> None:
    # Byte-stability guarantee: bumping to v3 changes only the top-level version string for a
    # Python-only baseline (no identity, no per-entry language).
    report = RiskReport(functions=(_fn("a.py", "f", "python", "fp1"),), files=())
    v3 = tmp_path / "v3.json"
    save_baseline(baseline_from_report(report), v3)
    v3_text = v3.read_text(encoding="utf-8")
    v2_text = v3_text.replace('"version": "3"', '"version": "2"', 1)
    # The only difference between the two is that one line.
    assert v3_text != v2_text
    assert v3_text.replace('"3"', '"2"', 1) == v2_text


def test_typescript_baseline_records_language_and_identity(tmp_path: Path) -> None:
    pytest.importorskip("tree_sitter_typescript")
    report = RiskReport(
        functions=(_fn("a.py", "f", "python", "fp1"), _fn("s.ts", "g", "typescript", "fp2")), files=()
    )
    baseline = baseline_from_report(report)
    assert set(baseline.identity) == {"typescript"}
    assert baseline.identity["typescript"]["scheme"] == 1
    assert baseline.identity["typescript"]["grammar"]  # the installed grammar version
    out = tmp_path / "b.json"
    save_baseline(baseline, out)
    raw = json.loads(out.read_text(encoding="utf-8"))
    by_name = {e["qualname"]: e for e in raw["entries"]}
    assert "language" not in by_name["f"]  # python omitted
    assert by_name["g"]["language"] == "typescript"
    # Round-trips: identity + language survive load.
    loaded = load_baseline(out)
    assert loaded.identity == baseline.identity
    assert loaded.entries[FunctionId("s.ts", "g")].language == "typescript"


def test_v2_baseline_loads_as_all_python(tmp_path: Path) -> None:
    v2 = tmp_path / "v2.json"
    v2.write_text(
        json.dumps(
            {
                "version": "2",
                "entries": [
                    {
                        "path": "a.py",
                        "qualname": "f",
                        "score": 10.0,
                        "components": {
                            "coverage_gap": 0.0,
                            "structural_complexity": 0.0,
                            "branch_gap": 0.0,
                            "churn": 0.0,
                            "public_surface": 0.0,
                            "sprawl": 0.0,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    loaded = load_baseline(v2)
    assert loaded.version == "2"
    assert loaded.identity == {}
    assert loaded.entries[FunctionId("a.py", "f")].language == "python"


def test_typescript_identity_stale_detects_grammar_bump() -> None:
    entries = {FunctionId("s.ts", "g"): _baseline_entry("s.ts", "g", "typescript")}
    fresh = Baseline(version="3", entries=entries, identity={"typescript": {"scheme": 1, "grammar": "0.0.0"}})
    # A grammar that can't match the runtime's is stale.
    assert typescript_identity_stale(fresh) is True
    # No TS identity recorded → never stale.
    assert typescript_identity_stale(Baseline(version="3", entries={}, identity={})) is False


def test_suppress_stale_typescript_renames_clears_only_ts(tmp_path: Path) -> None:
    entries = {
        FunctionId("a.py", "f"): _baseline_entry("a.py", "f", "python"),
        FunctionId("s.ts", "g"): _baseline_entry("s.ts", "g", "typescript"),
    }
    baseline = Baseline(version="3", entries=entries)
    report = RiskReport(
        functions=(_fn("a.py", "f", "python", "fp1"), _fn("s.ts", "g", "typescript", "fp2")), files=()
    )
    new_baseline, new_report = suppress_stale_typescript_renames(baseline, report)
    # Python fingerprints preserved; TypeScript fingerprints cleared (id-only matching).
    assert new_baseline.entries[FunctionId("a.py", "f")].fingerprint is not None
    assert new_baseline.entries[FunctionId("s.ts", "g")].fingerprint is None
    by_lang = {fn.language: fn for fn in new_report.functions}
    assert by_lang["python"].fingerprint is not None
    assert by_lang["typescript"].fingerprint is None


def _baseline_entry(path: str, name: str, language: str) -> BaselineEntry:
    return BaselineEntry(
        id=FunctionId(path=path, qualname=name),
        score=10.0,
        components=RiskComponents(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        fingerprint=f"fp:{name}",
        signature=f"sig:{name}",
        language=language,
    )
