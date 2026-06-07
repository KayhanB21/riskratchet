"""Phase-B reproducibility check: run each repo's defect pipeline a SECOND time
with fresh coverage and confirm the outputs are byte-identical to the first run.

For each repo: hash the current defect-labels.json (run 1) and the cached scored
snapshot analyze.json; delete the coverage artifacts (analyze/coverage/meta) so the
suite re-runs under fresh coverage; re-run `defects`; hash again (run 2). Labels are
coverage-independent (parse + SZZ blame + fingerprint) so they must match; analyze.json
carries the coverage-derived scores, so a match there proves the suite/coverage/scoring
path is deterministic. Worktree + venv are kept, so only the suite re-runs.

Usage: uv run python -m bin.calibration.verify_repro <repo> [<repo> ...]
Writes/updates data/calibration/repro-verification.json.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from bin.calibration.corpus import CALIBRATION_DIR
from bin.calibration.coverage_replay import revision_cache_dir

REPOS_DIR = CALIBRATION_DIR / "repos"
RESULT_PATH = CALIBRATION_DIR / "repro-verification.json"


def _hash(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _scores(analyze: Path) -> dict[str, float] | None:
    """Semantic score vector per function id (robust to key-order/formatting)."""
    if not analyze.exists():
        return None
    data = json.loads(analyze.read_text())
    out: dict[str, float] = {}
    for fn in data.get("functions", []):
        fid = fn.get("id", {})
        key = f"{fid.get('path')}::{fid.get('qualname')}"
        out[key] = round(float(fn.get("score", 0.0)), 6)
    return out


def verify(repo: str) -> dict[str, object]:
    lbl = REPOS_DIR / repo / "defect-labels.json"
    if not lbl.exists():
        return {"repo": repo, "status": "NO_LABELS"}
    sha = json.loads(lbl.read_text())["snapshot_sha"]
    cache = revision_cache_dir(repo, sha)
    analyze = cache / "analyze.json"

    lbl1 = _hash(lbl)
    scores1 = _scores(analyze)

    for name in ("analyze.json", "coverage.json", "meta.json"):
        (cache / name).unlink(missing_ok=True)

    proc = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "-m",
            "bin.calibration.harness",
            "defects",
            "--repos",
            repo,
            "--snapshot-days",
            "365",
            "--max-fixes",
            "60",
        ],
        capture_output=True,
        text=True,
    )
    lbl2 = _hash(lbl)
    scores2 = _scores(analyze)

    labels_match = lbl1 is not None and lbl1 == lbl2
    if scores1 is None or scores2 is None:
        scores_match: object = "RUN2_FAILED" if scores2 is None else "NO_RUN1_CACHE"
        n_drift = None
    else:
        drift = [k for k in scores1 if scores2.get(k) != scores1[k]]
        added = set(scores2) - set(scores1)
        removed = set(scores1) - set(scores2)
        scores_match = not drift and not added and not removed
        n_drift = len(drift) + len(added) + len(removed)
    status = "VERIFIED" if labels_match and scores_match is True else "MISMATCH"
    return {
        "repo": repo,
        "status": status,
        "labels_match": labels_match,
        "scores_match": scores_match,
        "n_score_drift": n_drift,
        "n_functions": len(scores2) if scores2 else None,
        "rerun_ok": proc.returncode == 0 and scores2 is not None,
    }


def main(argv: list[str]) -> int:
    results: dict[str, dict[str, object]] = {}
    if RESULT_PATH.exists():
        results = json.loads(RESULT_PATH.read_text())
    for repo in argv:
        r = verify(repo)
        results[repo] = r
        print(
            f"{r['status']:>9}  {repo:<20} labels={r.get('labels_match')} "
            f"scores={r.get('scores_match')} drift={r.get('n_score_drift')}",
            file=sys.stderr,
        )
        RESULT_PATH.write_text(json.dumps(dict(sorted(results.items())), indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
