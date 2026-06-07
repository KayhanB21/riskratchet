"""Coverage-free snapshot scoring: score every function at `S` from source + git alone.

Mirrors `defects.score_snapshot` but **skips the venv/test-suite entirely**, so it runs on
untested repos (the actual target population). With no coverage the two coverage-derived
components are constant (`coverage_gap`=100, `branch_gap`=0) and are dropped by the
proneness model; the four static signals (`structural_complexity`, `sprawl`,
`public_surface`, `churn`) carry the analysis. Cached under a separate
`analyze-coverage-free.json` so the coverage-based `analyze.json` (used by `predict`/
`ablate`) is never clobbered.
"""

from __future__ import annotations

import json
from dataclasses import replace

from bin.calibration.config import RepoConfig
from bin.calibration.corpus import analyze_report
from bin.calibration.coverage_replay import _prepare_worktree, ensure_clone, revision_cache_dir
from bin.calibration.defects import SnapshotPopulation
from bin.calibration.git_checkout import CommandRunner, default_runner
from bin.calibration.serial import report_from_dict, report_to_dict
from riskratchet.models import RiskReport
from riskratchet.scoring import total_risk

# Coverage components zeroed; the other four renormalized to sum to 1. The model reads raw
# components, so this only sets each function's cosmetic `score` in the cached file (honest:
# a coverage-free total, not a pessimistic-coverage one).
_STATIC_BASE = {"structural_complexity": 0.25, "churn": 0.10, "public_surface": 0.10, "sprawl": 0.10}
_DENOM = sum(_STATIC_BASE.values())
COVERAGE_FREE_WEIGHTS: dict[str, float] = {
    "coverage_gap": 0.0,
    "branch_gap": 0.0,
    **{key: value / _DENOM for key, value in _STATIC_BASE.items()},
}

ANALYZE_COVERAGE_FREE = "analyze-coverage-free.json"


def recompute_coverage_free_total(report: RiskReport) -> RiskReport:
    fns = tuple(
        replace(fn, score=total_risk(fn.components, weights=COVERAGE_FREE_WEIGHTS)) for fn in report.functions
    )
    return replace(report, functions=fns)


def score_snapshot_coverage_free(
    repo: RepoConfig, sha: str, *, run: CommandRunner = default_runner, force: bool = False
) -> SnapshotPopulation | None:
    """Check out `S` (no test run) and score it coverage-free. Cached per SHA."""
    cache = revision_cache_dir(repo.name, sha)
    cached = cache / ANALYZE_COVERAGE_FREE
    if not force and cached.exists():
        report = report_from_dict(json.loads(cached.read_text(encoding="utf-8")))
        return SnapshotPopulation(snapshot_sha=sha, report=report)

    clone = ensure_clone(repo, run=run)
    if clone is None:
        return None
    worktree = cache / "worktree"
    if not _prepare_worktree(clone, sha, worktree, run=run):
        return None
    paths = [worktree / p for p in repo.paths] if repo.paths else [worktree]
    report = recompute_coverage_free_total(analyze_report(paths, worktree))
    cache.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(report_to_dict(report), indent=2) + "\n", encoding="utf-8")
    return SnapshotPopulation(snapshot_sha=sha, report=report)
