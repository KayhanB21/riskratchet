"""Structural validation of `.github/workflows/*.yml`.

The 0.2.5 release added two new workflow files (`baseline-gate.yml`) and
extended `ci.yml` with a `top-risk` job. These tests catch
syntactic-and-shape errors that would only otherwise be visible after
opening a PR. They deliberately do not execute the workflows; for full
end-to-end verification, see the post-PR smoke checklist in
`CONTRIBUTING.md`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"
BASELINE_GATE = WORKFLOWS_DIR / "baseline-gate.yml"
CI = WORKFLOWS_DIR / "ci.yml"


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_baseline_gate_yaml_loads_and_has_required_shape() -> None:
    payload = _load_yaml(BASELINE_GATE)
    assert "jobs" in payload
    jobs = payload["jobs"]
    assert "baseline-rationale" in jobs
    assert "steps" in jobs["baseline-rationale"]
    assert payload.get("on") == {"pull_request": None} or "pull_request" in str(payload.get(True, ""))
    # permissions must restrict to read (we never write to the repo)
    assert payload.get("permissions") == {"contents": "read"}


def test_baseline_gate_invokes_the_rationale_script() -> None:
    payload = _load_yaml(BASELINE_GATE)
    steps = payload["jobs"]["baseline-rationale"]["steps"]
    script_step = next(
        (s for s in steps if "check_baseline_rationale.py" in (s.get("run") or "")),
        None,
    )
    assert script_step is not None, "the gate must call bin/check_baseline_rationale.py"
    env = script_step.get("env", {})
    for required in ("BASE_SHA", "HEAD_SHA", "PR_BODY", "PR_LABELS"):
        assert required in env, f"{required} must be passed via env"


def test_ci_top_risk_job_uploads_artifact() -> None:
    payload = _load_yaml(CI)
    job = payload["jobs"].get("top-risk")
    assert job is not None, "ci.yml must define a top-risk job"
    assert job.get("if") == "github.event_name == 'pull_request'"
    steps = job["steps"]
    upload = next(
        (s for s in steps if "upload-artifact" in (s.get("uses") or "")),
        None,
    )
    assert upload is not None, "top-risk must upload its report as an artifact"
    with_block = upload.get("with") or {}
    assert with_block.get("name") == "top-risk"
    paths = str(with_block.get("path") or "")
    assert "docs/top-risk.md" in paths
    assert "docs/top-risk.json" in paths


_PINNED_USES_RE = re.compile(r"^[^/]+/[^@]+@[a-f0-9]{40}\b")


@pytest.mark.parametrize("workflow", [BASELINE_GATE, CI])
def test_workflows_pin_actions_to_full_commit_sha(workflow: Path) -> None:
    """Security posture: every `uses:` must pin to a 40-char SHA, never a
    floating tag like @v3. The optional ` # vN.M.K` comment is allowed
    (and encouraged) after the SHA."""
    payload = _load_yaml(workflow)
    jobs = payload.get("jobs", {}) or {}
    uses_values: list[str] = []
    for job in jobs.values():
        for step in job.get("steps", []) or []:
            value = step.get("uses")
            if value:
                uses_values.append(str(value))
        # GitHub Actions also allows `uses:` on the job itself
        job_uses = job.get("uses")
        if job_uses:
            uses_values.append(str(job_uses))
    assert uses_values, f"no `uses:` entries found in {workflow}"
    for value in uses_values:
        assert _PINNED_USES_RE.match(value), (
            f"workflow {workflow.name} uses unpinned action {value!r}; must pin to a 40-char commit SHA"
        )
