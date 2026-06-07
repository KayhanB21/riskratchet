"""Calibration harness CLI (P21).

Subcommands:

  replay    Phase 1. Replay recent merged PRs (base/head under coverage, cached),
            diff head vs base, join manual labels -> `pr-replay-rollup.json`.
  rescore   Phase 1. Re-score labelled PRs under each sprawl candidate and write
            the accept/reject separation -> `sprawl-candidates.json`.
  defects   Phase 2. Mine SZZ defect labels at a historical snapshot S (score S
            under coverage, blame bug-fixes to the introducing function, track
            back to S) -> `defect-labels.json`.
  predict   Phase 2. Per sprawl candidate, AUC of the score vs the SZZ defect
            label -> `defect-prediction.json`.
  ablate    Phase 3. Pooled, repo-stratified logistic-regression ablation of the
            file-line sprawl term (the decision-gate model) -> `ablation.json`.
  proneness Phase 3 (maintainability probe). Coverage-free change-proneness labels
            from git history (no test suite) -> `change-proneness-labels.json`.
  proneness-model  Phase 3. Null (past-churn) vs full (structural) model on the
            change-prone label -> `proneness-ablation.json`.
  review-flags     Phase 3 anchor. Mine "split this" PR review comments (needs gh)
            -> `review-flags.json`.

These are human-run local steps: they clone real repos and run their suites under
coverage (minutes, needs network; `replay`/`review-flags` also need `gh`). None run
in CI; the test suite exercises the pieces hermetically. `ablate` /
`proneness-model` additionally need the calibration dependency group (scipy/numpy):

  uv run python -m bin.calibration.harness defects --repos requests --snapshot-days 365
  uv run python -m bin.calibration.harness predict
  uv run --group calibration python -m bin.calibration.harness ablate
  uv run python -m bin.calibration.harness proneness --repos requests
  uv run --group calibration python -m bin.calibration.harness proneness-model
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

from bin.calibration.config import REPOS_DIR, RepoConfig, load_corpus, load_labels
from bin.calibration.corpus import CALIBRATION_DIR
from bin.calibration.coverage_replay import replay_revision, revision_cache_dir
from bin.calibration.defects import DefectLabels, SnapshotPopulation, collect_defect_labels
from bin.calibration.predict import evaluate_candidates
from bin.calibration.prs import enumerate_merged_prs
from bin.calibration.replay import OutcomeRecord, join_label, replay_reports
from bin.calibration.rescore import LabeledPr, evaluate
from bin.calibration.serial import report_from_dict
from riskratchet.models import FunctionId, RiskReport

ROLLUP_PATH = CALIBRATION_DIR / "pr-replay-rollup.json"
CANDIDATES_PATH = CALIBRATION_DIR / "sprawl-candidates.json"
ABLATION_PATH = CALIBRATION_DIR / "ablation.json"
PRONENESS_MODEL_PATH = CALIBRATION_DIR / "proneness-ablation.json"


def defect_labels_path(repo_name: str) -> Path:
    return REPOS_DIR / repo_name / "defect-labels.json"


def defect_prediction_path(repo_name: str) -> Path:
    return REPOS_DIR / repo_name / "defect-prediction.json"


def change_proneness_labels_path(repo_name: str) -> Path:
    return REPOS_DIR / repo_name / "change-proneness-labels.json"


def review_flags_path(repo_name: str) -> Path:
    return REPOS_DIR / repo_name / "review-flags.json"


_PREDICTION_NOTE = (
    "AUC of the total score and of the sprawl component alone, per sprawl "
    "candidate, against the SZZ defect label. total_auc(drop_file_line) > "
    "total_auc(baseline) => the file-line term is noise; lower => signal. "
    "sprawl_auc ~ 0.5 => the component is non-predictive. Directional until the "
    "corpus is pooled; SZZ precision and right-censoring caveats apply (see "
    "data/calibration/README.md)."
)


def _select_repos(repos: list[RepoConfig], only: set[str] | None) -> list[RepoConfig]:
    enabled = [r for r in repos if r.replay_enabled]
    if only is not None:
        enabled = [r for r in enabled if r.name in only]
    return enabled


def _select_proneness_repos(repos: list[RepoConfig], only: set[str] | None) -> list[RepoConfig]:
    # Coverage-free proneness runs on the defect corpus (re-scored coverage-free) AND
    # the gradient cohort (coverage_free repos with no test suite).
    selected = [r for r in repos if r.replay_enabled or r.coverage_free]
    if only is not None:
        selected = [r for r in selected if r.name in only]
    return selected


def cmd_replay(args: argparse.Namespace) -> int:
    repos = _select_repos(load_corpus(), set(args.repos.split(",")) if args.repos else None)
    if not repos:
        print("no replay-enabled repos selected", file=sys.stderr)
        return 1
    labels = load_labels()
    records: list[OutcomeRecord] = []
    start = time.monotonic()

    for repo in repos:
        prs = enumerate_merged_prs(repo, args.max_prs)
        print(f"{repo.name}: {len(prs)} merged PRs", file=sys.stderr)
        for pr in prs:
            if args.time_budget_seconds and time.monotonic() - start > args.time_budget_seconds:
                print(f"time budget exhausted; stopping after {len(records)} PRs", file=sys.stderr)
                break
            base = replay_revision(repo, pr.base_sha, force=args.force)
            head = replay_revision(repo, pr.head_sha, force=args.force)
            if base.report is None or head.report is None:
                print(f"  skip {repo.name}#{pr.number}: unusable coverage", file=sys.stderr)
                continue
            record = replay_reports(
                repo=repo.name,
                pr=pr.number,
                base_sha=pr.base_sha,
                head_sha=pr.head_sha,
                base_report=base.report,
                head_report=head.report,
            )
            record = replace(
                record,
                base_tests_failed=base.tests_failed,
                head_tests_failed=head.tests_failed,
                base_usable_coverage=base.usable_coverage,
                head_usable_coverage=head.usable_coverage,
            )
            records.append(join_label(record, labels))

    _write_rollup(records)
    print(f"wrote {ROLLUP_PATH.name} ({len(records)} PRs)")
    return 0


def _write_rollup(records: list[OutcomeRecord]) -> None:
    ordered = sorted(records, key=lambda r: (r.repo, r.pr))
    n_labeled = sum(1 for r in ordered if r.label != "unlabeled")
    payload = {
        "schema": 1,
        "summary": {
            "n_prs": len(ordered),
            "n_labeled": n_labeled,
            "repos": sorted({r.repo for r in ordered}),
        },
        "records": [r.to_digest() for r in ordered],
    }
    ROLLUP_PATH.parent.mkdir(parents=True, exist_ok=True)
    ROLLUP_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def cmd_rescore(args: argparse.Namespace) -> int:
    if not ROLLUP_PATH.exists():
        print(f"no rollup at {ROLLUP_PATH}; run `replay` first", file=sys.stderr)
        return 1
    rollup = json.loads(ROLLUP_PATH.read_text(encoding="utf-8"))
    records = rollup.get("records", [])
    labeled = [r for r in records if r.get("label") in {"accepted", "rejected"}]

    prs: list[LabeledPr] = []
    for rec in labeled:
        base = _load_cached_report(rec["repo"], rec["base_sha"])
        head = _load_cached_report(rec["repo"], rec["head_sha"])
        if base is None or head is None:
            print(f"  skip {rec['repo']}#{rec['pr']}: analyze cache missing", file=sys.stderr)
            continue
        prs.append(
            LabeledPr(repo=rec["repo"], pr=rec["pr"], label=rec["label"], base_report=base, head_report=head)
        )

    results = evaluate(prs)
    payload = {
        "schema": 1,
        "n_labeled_prs": len(prs),
        "note": (
            "Phase-1 proxy: the accept/reject label is hand-labelled and the "
            "rejected class is near-empty in merge history, so the separation "
            "below is a smoke test of the machinery, NOT evidence about sprawl. "
            "Intended successor: SZZ defect-linking (see "
            "data/calibration/README.md). effect/z > 0 means rejected PRs carry "
            "more regressions than accepted ones under that candidate."
        ),
        "candidates": results,
    }
    CANDIDATES_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {CANDIDATES_PATH.name} ({len(prs)} labelled PRs)")
    return 0


def _load_cached_report(repo: str, sha: str) -> RiskReport | None:
    analyze_path = revision_cache_dir(repo, sha) / "analyze.json"
    if not analyze_path.exists():
        return None
    return report_from_dict(json.loads(analyze_path.read_text(encoding="utf-8")))


def _labels_to_dict(labels: DefectLabels) -> dict[str, object]:
    return {
        "snapshot_sha": labels.snapshot_sha,
        "head_sha": labels.head_sha,
        "window_days": labels.window_days,
        "n_functions": labels.n_functions,
        "n_defect_functions": labels.n_defect_functions,
        "n_fixes_scanned": labels.n_fixes_scanned,
        "n_fixes_blamed": labels.n_fixes_blamed,
        "n_implications_untracked": labels.n_implications_untracked,
        "labels": [
            {"target": fid.as_target(), "defect_count": count}
            for fid, count in sorted(labels.counts.items(), key=lambda kv: kv[0].as_target())
        ],
    }


def _labels_from_dict(repo: str, data: dict[str, object]) -> DefectLabels:
    rows = data["labels"]
    assert isinstance(rows, list)
    counts: dict[FunctionId, int] = {}
    for row in rows:
        path, _, qualname = str(row["target"]).partition("::")
        counts[FunctionId(path, qualname)] = _as_int(row["defect_count"])
    return DefectLabels(
        repo=repo,
        snapshot_sha=str(data["snapshot_sha"]),
        head_sha=str(data["head_sha"]),
        window_days=_as_int(data["window_days"]),
        n_functions=_as_int(data["n_functions"]),
        n_fixes_scanned=_as_int(data["n_fixes_scanned"]),
        n_fixes_blamed=_as_int(data["n_fixes_blamed"]),
        n_implications_untracked=_as_int(data["n_implications_untracked"]),
        counts=counts,
    )


def _as_int(value: object) -> int:
    assert isinstance(value, int)
    return value


def cmd_defects(args: argparse.Namespace) -> int:
    repos = _select_repos(load_corpus(), set(args.repos.split(",")) if args.repos else None)
    if not repos:
        print("no replay-enabled repos selected", file=sys.stderr)
        return 1
    written = 0
    for repo in repos:
        _, labels = collect_defect_labels(
            repo,
            snapshot_sha_override=args.snapshot_sha,
            snapshot_days=args.snapshot_days,
            window_days=args.window_days,
            max_fixes=args.max_fixes,
        )
        if labels is None:
            print(f"  skip {repo.name}: snapshot unscored / clone failed", file=sys.stderr)
            continue
        path = defect_labels_path(repo.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Per-repo file: a run for one repo only rewrites that repo's folder.
        path.write_text(json.dumps(_labels_to_dict(labels), indent=2) + "\n", encoding="utf-8")
        written += 1
        print(
            f"{repo.name}: {labels.n_defect_functions}/{labels.n_functions} defect functions "
            f"from {labels.n_fixes_blamed} fixes ({labels.n_implications_untracked} untracked)",
            file=sys.stderr,
        )
    print(f"wrote defect-labels.json for {written} repo(s)")
    return 0


def cmd_predict(args: argparse.Namespace) -> int:
    label_files = sorted(REPOS_DIR.glob("*/defect-labels.json"))
    if not label_files:
        print("no per-repo defect-labels.json found; run `defects` first", file=sys.stderr)
        return 1
    written = 0
    for label_file in label_files:
        repo_name = label_file.parent.name
        raw = json.loads(label_file.read_text(encoding="utf-8"))
        report = _load_cached_report(repo_name, str(raw["snapshot_sha"]))
        if report is None:
            print(f"  skip {repo_name}: snapshot analyze cache missing", file=sys.stderr)
            continue
        labels = _labels_from_dict(repo_name, raw)
        snapshot = SnapshotPopulation(snapshot_sha=labels.snapshot_sha, report=report)
        results = evaluate_candidates(snapshot, labels)
        payload = {
            "schema": 1,
            "note": _PREDICTION_NOTE,
            "n_buggy": labels.n_defect_functions,
            "n_clean": labels.n_functions - labels.n_defect_functions,
            "coverage": "full (snapshot replayed under coverage)",
            "candidates": [r.to_dict() for r in results],
        }
        defect_prediction_path(repo_name).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        written += 1
    print(f"wrote defect-prediction.json for {written} repo(s)")
    return 0


def cmd_proneness(args: argparse.Namespace) -> int:
    from bin.calibration.proneness import collect_proneness_labels, labels_to_dict

    repos = _select_proneness_repos(load_corpus(), set(args.repos.split(",")) if args.repos else None)
    if not repos:
        print("no proneness-eligible repos selected", file=sys.stderr)
        return 1
    written = 0
    for repo in repos:
        _, labels = collect_proneness_labels(
            repo,
            snapshot_sha_override=args.snapshot_sha,
            snapshot_days=args.snapshot_days,
            window_days=args.window_days,
        )
        if labels is None:
            print(f"  skip {repo.name}: snapshot unscored / clone failed", file=sys.stderr)
            continue
        path = change_proneness_labels_path(repo.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(labels_to_dict(labels), indent=2) + "\n", encoding="utf-8")
        written += 1
        note = " (short past history)" if labels.insufficient_past_history else ""
        print(
            f"{repo.name}: {labels.n_change_prone}/{labels.n_functions} change-prone "
            f"from {labels.n_future_commits} future commits{note}",
            file=sys.stderr,
        )
    print(f"wrote change-proneness-labels.json for {written} repo(s)")
    return 0


def cmd_ablate(args: argparse.Namespace) -> int:
    # Imported lazily so scipy/numpy (the calibration dependency group) are only
    # required on this code path, never for the other subcommands or the test suite.
    from bin.calibration.ablation import build_dataset, run_ablation, to_payload

    label_files = sorted(REPOS_DIR.glob("*/defect-labels.json"))
    only = set(args.repos.split(",")) if args.repos else None
    per_repo: list[tuple[str, RiskReport, set[FunctionId]]] = []
    skipped = 0
    for label_file in label_files:
        repo_name = label_file.parent.name
        if only is not None and repo_name not in only:
            continue
        raw = json.loads(label_file.read_text(encoding="utf-8"))
        report = _load_cached_report(repo_name, str(raw["snapshot_sha"]))
        if report is None:
            print(f"  skip {repo_name}: snapshot analyze cache missing", file=sys.stderr)
            skipped += 1
            continue
        labels = _labels_from_dict(repo_name, raw)
        per_repo.append((repo_name, report, set(labels.counts)))
    if not per_repo:
        print("no repos with a cached snapshot; run `defects` first", file=sys.stderr)
        return 1
    dataset = build_dataset(per_repo)
    result = run_ablation(dataset, l2=args.l2, bootstrap_draws=args.bootstrap_draws)
    payload = to_payload(result)
    ABLATION_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote {ABLATION_PATH.name}: {len(dataset.repos)} repos, "
        f"{dataset.n_functions} functions, {dataset.n_buggy} buggy "
        f"({skipped} repo(s) skipped for missing cache)"
    )
    return 0


def cmd_review_flags(args: argparse.Namespace) -> int:
    from bin.calibration.coverage_free import score_snapshot_coverage_free
    from bin.calibration.defects import resolve_snapshot
    from bin.calibration.git_checkout import ensure_clone
    from bin.calibration.review_comments import flags_to_dict, mine_review_flags

    repos = _select_proneness_repos(load_corpus(), set(args.repos.split(",")) if args.repos else None)
    if not repos:
        print("no proneness-eligible repos selected", file=sys.stderr)
        return 1
    written = 0
    for repo in repos:
        clone = ensure_clone(repo)
        if clone is None:
            print(f"  skip {repo.name}: clone failed", file=sys.stderr)
            continue
        sha = resolve_snapshot(
            clone, repo, snapshot_sha_override=args.snapshot_sha, snapshot_days=args.snapshot_days
        )
        if sha is None:
            print(f"  skip {repo.name}: no snapshot", file=sys.stderr)
            continue
        snapshot = score_snapshot_coverage_free(repo, sha)
        if snapshot is None:
            print(f"  skip {repo.name}: snapshot unscored", file=sys.stderr)
            continue
        flags = mine_review_flags(repo, clone, snapshot, max_prs=args.max_prs)
        review_flags_path(repo.name).write_text(
            json.dumps(flags_to_dict(flags), indent=2) + "\n", encoding="utf-8"
        )
        written += 1
        print(
            f"{repo.name}: {len(flags.counts)} flagged functions from "
            f"{flags.n_maintainability_comments}/{flags.n_comments_scanned} maintainability comments",
            file=sys.stderr,
        )
    print(f"wrote review-flags.json for {written} repo(s)")
    return 0


def cmd_proneness_model(args: argparse.Namespace) -> int:
    from bin.calibration.coverage_free import ANALYZE_COVERAGE_FREE
    from bin.calibration.proneness import labels_from_dict
    from bin.calibration.proneness_model import build_proneness_dataset, run_proneness, to_payload

    label_files = sorted(REPOS_DIR.glob("*/change-proneness-labels.json"))
    if not label_files:
        print("no change-proneness-labels.json found; run `proneness` first", file=sys.stderr)
        return 1
    tier_of = {r.name: ("messy" if r.coverage_free else "polished") for r in load_corpus()}
    per_repo = []
    for label_file in label_files:
        repo_name = label_file.parent.name
        raw = json.loads(label_file.read_text(encoding="utf-8"))
        cache = revision_cache_dir(repo_name, str(raw["snapshot_sha"])) / ANALYZE_COVERAGE_FREE
        if not cache.exists():
            print(f"  skip {repo_name}: coverage-free snapshot cache missing", file=sys.stderr)
            continue
        report = report_from_dict(json.loads(cache.read_text(encoding="utf-8")))
        labels = labels_from_dict(repo_name, raw)
        per_repo.append(
            (repo_name, SnapshotPopulation(snapshot_sha=labels.snapshot_sha, report=report), labels)
        )
    if not per_repo:
        print("no repos with a cached coverage-free snapshot", file=sys.stderr)
        return 1
    tiers = {name: tier_of.get(name, "polished") for name, _, _ in per_repo}
    dataset = build_proneness_dataset(per_repo)
    result = run_proneness(dataset, tiers, l2=args.l2, bootstrap_draws=args.bootstrap_draws)
    PRONENESS_MODEL_PATH.write_text(json.dumps(to_payload(result), indent=2) + "\n", encoding="utf-8")
    print(
        f"wrote {PRONENESS_MODEL_PATH.name}: {len(dataset.repos)} repos, "
        f"{dataset.n_functions} functions, {dataset.n_buggy} change-prone"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bin.calibration.harness")
    sub = parser.add_subparsers(dest="command", required=True)

    replay = sub.add_parser("replay", help="replay merged PRs and write the rollup")
    replay.add_argument("--repos", default="", help="comma-separated subset of enabled repos")
    replay.add_argument("--max-prs", type=int, default=10, help="PRs per repo (default 10)")
    replay.add_argument("--time-budget-seconds", type=int, default=0, help="soft wall-clock cap (0 = none)")
    replay.add_argument("--force", action="store_true", help="ignore the per-SHA cache")
    replay.set_defaults(func=cmd_replay)

    rescore = sub.add_parser("rescore", help="re-score labelled PRs under sprawl candidates")
    rescore.set_defaults(func=cmd_rescore)

    defects = sub.add_parser("defects", help="mine SZZ defect labels at a snapshot")
    defects.add_argument("--repos", default="", help="comma-separated subset of enabled repos")
    defects.add_argument(
        "--snapshot-sha", default="", help="pin snapshot S (else derive from --snapshot-days)"
    )
    defects.add_argument("--snapshot-days", type=int, default=365, help="S = this many days before HEAD")
    defects.add_argument(
        "--window-days", type=int, default=365, help="recorded defect window (informational)"
    )
    defects.add_argument("--max-fixes", type=int, default=100, help="cap fixes blamed per repo")
    defects.set_defaults(func=cmd_defects)

    predict = sub.add_parser("predict", help="AUC of score vs SZZ defect label per candidate")
    predict.set_defaults(func=cmd_predict)

    proneness = sub.add_parser(
        "proneness", help="phase 3: change-proneness labels (coverage-free, git-history only)"
    )
    proneness.add_argument("--repos", default="", help="comma-separated subset")
    proneness.add_argument("--snapshot-sha", default="", help="pin snapshot S (else from --snapshot-days)")
    proneness.add_argument("--snapshot-days", type=int, default=365, help="S = this many days before HEAD")
    proneness.add_argument("--window-days", type=int, default=365, help="past/future window length")
    proneness.set_defaults(func=cmd_proneness)

    ablate = sub.add_parser(
        "ablate", help="pooled, repo-stratified logistic-regression ablation of the file-line term"
    )
    ablate.add_argument("--repos", default="", help="comma-separated subset of enabled repos")
    ablate.add_argument("--l2", type=float, default=1.0, help="L2 regularization strength (default 1.0)")
    ablate.add_argument(
        "--bootstrap-draws", type=int, default=1000, help="repo-clustered bootstrap draws (default 1000)"
    )
    ablate.set_defaults(func=cmd_ablate)

    pmodel = sub.add_parser(
        "proneness-model", help="phase 3: structure-beats-activity null-vs-full model on change-proneness"
    )
    pmodel.add_argument("--l2", type=float, default=1.0, help="L2 regularization strength (default 1.0)")
    pmodel.add_argument(
        "--bootstrap-draws", type=int, default=1000, help="repo-clustered bootstrap draws (default 1000)"
    )
    pmodel.set_defaults(func=cmd_proneness_model)

    review = sub.add_parser(
        "review-flags", help="phase 3 anchor: mine 'split this' PR review comments (needs gh)"
    )
    review.add_argument("--repos", default="", help="comma-separated subset")
    review.add_argument("--snapshot-sha", default="", help="pin snapshot S (else from --snapshot-days)")
    review.add_argument("--snapshot-days", type=int, default=365, help="S = this many days before HEAD")
    review.add_argument("--max-prs", type=int, default=100, help="merged PRs to scan per repo")
    review.set_defaults(func=cmd_review_flags)
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
