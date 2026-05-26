#!/usr/bin/env python3
"""Gate PRs that mutate `.riskratchet.json` without a rationale.

Designed to run inside the `baseline-gate.yml` GitHub Actions job, but
written so its core (`baseline_changed`, `parse_rationale`, `is_bypassed`,
`evaluate`) is importable for unit tests.

Inputs (all via env vars set by the workflow):

- `BASE_SHA`: the merge base; `git diff --name-only $BASE_SHA $HEAD_SHA`
- `HEAD_SHA`: PR head SHA
- `PR_BODY`: the pull request body (may be empty)
- `PR_LABELS`: comma-separated list of labels on the PR
- `RR_BASELINE_PATH` (optional): override path; defaults to `.riskratchet.json`

Exit codes:

- `0` baseline unchanged, or rationale provided, or bypass accepted.
- `1` baseline changed and rationale missing.
- `2` unexpected error (git invocation failure, malformed env).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass

BYPASS_LABEL = "baseline-approved"
COMMIT_BYPASS_TOKEN = "[riskratchet-baseline-bypass]"
RATIONALE_HEADING_RE = re.compile(r"^##+\s*Baseline\s+bump\s+rationale\s*$", re.IGNORECASE | re.MULTILINE)
INLINE_RATIONALE_RE = re.compile(r"^riskratchet-baseline-rationale:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
MIN_RATIONALE_LENGTH = 20

USAGE_HINT = (
    "Baseline changed without a rationale.\n"
    "Add one of the following to this PR:\n"
    f"  - a heading `## Baseline bump rationale` followed by >= "
    f"{MIN_RATIONALE_LENGTH} chars of explanation,\n"
    "  - an inline `riskratchet-baseline-rationale: <text>` line in the PR body,\n"
    f"  - the `{BYPASS_LABEL}` label,\n"
    f"  - or the commit-message token `{COMMIT_BYPASS_TOKEN}`."
)


@dataclass(frozen=True)
class Decision:
    exit_code: int
    message: str


def baseline_changed(base_sha: str, head_sha: str, baseline_path: str) -> bool:
    """Return True when `baseline_path` is in `git diff --name-only base..head`."""
    if not base_sha or not head_sha:
        raise ValueError("BASE_SHA and HEAD_SHA must be set")
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base_sha}..{head_sha}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git diff failed: {result.stderr.strip()}")
    return baseline_path in result.stdout.splitlines()


def parse_rationale(body: str) -> str | None:
    """Return the rationale text if the PR body contains one, else None.

    Accepts either a `## Baseline bump rationale` heading followed by >=
    `MIN_RATIONALE_LENGTH` non-whitespace characters, or an inline
    `riskratchet-baseline-rationale: <text>` line.
    """
    if not body:
        return None
    inline = INLINE_RATIONALE_RE.search(body)
    if inline:
        text = inline.group(1).strip()
        if len(text) >= MIN_RATIONALE_LENGTH:
            return text
        return None
    heading = RATIONALE_HEADING_RE.search(body)
    if heading:
        rest = body[heading.end() :]
        stripped = re.sub(r"\s", "", rest)
        if len(stripped) >= MIN_RATIONALE_LENGTH:
            return rest.strip().splitlines()[0] if rest.strip() else None
    return None


def label_bypass(labels: str) -> bool:
    return BYPASS_LABEL in {label.strip() for label in labels.split(",") if label.strip()}


def commit_token_bypass(base_sha: str, head_sha: str) -> bool:
    if not base_sha or not head_sha:
        return False
    result = subprocess.run(
        ["git", "log", "--format=%B", f"{base_sha}..{head_sha}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return False
    return COMMIT_BYPASS_TOKEN in result.stdout


def is_bypassed(*, labels: str, base_sha: str, head_sha: str) -> str | None:
    """Return a human-readable bypass reason, or None if no bypass applies."""
    if label_bypass(labels):
        return f"bypassed by label {BYPASS_LABEL!r}"
    if commit_token_bypass(base_sha, head_sha):
        return f"bypassed by commit token {COMMIT_BYPASS_TOKEN!r}"
    return None


def evaluate(
    *,
    body: str,
    labels: str,
    base_sha: str,
    head_sha: str,
    baseline_path: str,
    changed_fn: object = None,
) -> Decision:
    """Pure-logic decision used by tests and the script entrypoint."""
    changed: bool
    if changed_fn is None:
        try:
            changed = baseline_changed(base_sha, head_sha, baseline_path)
        except (ValueError, RuntimeError) as exc:
            return Decision(exit_code=2, message=f"error: {exc}")
    else:
        changed = bool(changed_fn(base_sha, head_sha, baseline_path))  # type: ignore[operator]
    if not changed:
        return Decision(exit_code=0, message=f"{baseline_path} unchanged; gate satisfied.")
    bypass = is_bypassed(labels=labels, base_sha=base_sha, head_sha=head_sha)
    if bypass is not None:
        return Decision(exit_code=0, message=bypass)
    rationale = parse_rationale(body)
    if rationale:
        return Decision(exit_code=0, message=f"rationale accepted: {rationale[:80]!r}")
    return Decision(exit_code=1, message=USAGE_HINT)


def main() -> int:
    base_sha = os.environ.get("BASE_SHA", "")
    head_sha = os.environ.get("HEAD_SHA", "")
    body = os.environ.get("PR_BODY", "")
    labels = os.environ.get("PR_LABELS", "")
    baseline_path = os.environ.get("RR_BASELINE_PATH", ".riskratchet.json")
    decision = evaluate(
        body=body,
        labels=labels,
        base_sha=base_sha,
        head_sha=head_sha,
        baseline_path=baseline_path,
    )
    stream = sys.stdout if decision.exit_code == 0 else sys.stderr
    print(decision.message, file=stream)
    return decision.exit_code


if __name__ == "__main__":
    sys.exit(main())
