"""Corpus analysis helpers for the calibration harness.

Thin wrappers over ``riskratchet.engine.analyze`` so the replay and re-scoring
modules share one scoring entry point. Cloning/worktree/coverage orchestration
for external repos lives in ``coverage_replay.py``; this module is just
"paths (+ optional coverage) in, FunctionRisk out".
"""

from __future__ import annotations

from pathlib import Path

from riskratchet.engine import analyze
from riskratchet.models import FunctionRisk, RiskReport

REPO_ROOT = Path(__file__).resolve().parents[2]
CALIBRATION_DIR = REPO_ROOT / "data" / "calibration"
CORPUS_DIR = CALIBRATION_DIR / "corpus"
CACHE_DIR = CALIBRATION_DIR / "_cache"


def analyze_report(
    paths: list[Path],
    root: Path,
    *,
    coverage_path: Path | None = None,
) -> RiskReport:
    """Run the engine over ``paths`` and return the full report.

    ``use_git=False`` always: churn over an arbitrary OSS history is noise here
    and slow. Scores are deterministic given the source + coverage, which is what
    the PR-replay cache and candidate re-scoring rely on.
    """
    return analyze(paths, root=root, coverage_path=coverage_path, use_git=False)


def analyze_functions(
    paths: list[Path],
    root: Path,
    *,
    coverage_path: Path | None = None,
) -> list[FunctionRisk]:
    """Convenience wrapper returning just the per-function records."""
    return list(analyze_report(paths, root, coverage_path=coverage_path).functions)
