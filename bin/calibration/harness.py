"""Calibration harness CLI (P21, phase 1).

Two subcommands:

  replay   Enumerate recent merged PRs for each enabled corpus repo, replay base
           and head under coverage (cached per SHA), diff head vs base, join
           manual labels, and write the committed `pr-replay-rollup.json`.

  rescore  Re-score every labelled PR's cached reports under each sprawl candidate
           and write the accept/reject separation to `sprawl-candidates.json`.

This is a human-run local step: `replay` clones real repos and runs their test
suites under coverage (hours, needs `gh` auth + network). `rescore` is fast and
offline — it reads the per-SHA analyze cache `replay` left behind. Neither runs in
CI; the test suite exercises the pieces hermetically.

  uv run python -m bin.calibration.harness replay --repos requests --max-prs 5
  uv run python -m bin.calibration.harness rescore
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace

from bin.calibration.config import RepoConfig, load_corpus, load_labels
from bin.calibration.corpus import CALIBRATION_DIR
from bin.calibration.coverage_replay import replay_revision, revision_cache_dir
from bin.calibration.prs import enumerate_merged_prs
from bin.calibration.replay import OutcomeRecord, join_label, replay_reports
from bin.calibration.rescore import LabeledPr, evaluate
from bin.calibration.serial import report_from_dict
from riskratchet.models import RiskReport

ROLLUP_PATH = CALIBRATION_DIR / "pr-replay-rollup.json"
CANDIDATES_PATH = CALIBRATION_DIR / "sprawl-candidates.json"


def _select_repos(repos: list[RepoConfig], only: set[str] | None) -> list[RepoConfig]:
    enabled = [r for r in repos if r.replay_enabled]
    if only is not None:
        enabled = [r for r in enabled if r.name in only]
    return enabled


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
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
