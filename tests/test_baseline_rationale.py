"""Tests for the baseline-rationale gate script.

The script reads its inputs from environment variables (set by the
GitHub Actions workflow). To keep these tests fast and hermetic, we
import its core helpers and inject a fake `baseline_changed` callable
rather than running real `git diff`. One end-to-end integration test
drives the script against a tmp git repo as a regression guard.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BIN_DIR = Path(__file__).resolve().parent.parent / "bin"
SCRIPT = BIN_DIR / "check_baseline_rationale.py"


def _load_module() -> object:
    spec = importlib.util.spec_from_file_location("check_baseline_rationale", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_baseline_rationale"] = module
    spec.loader.exec_module(module)
    return module


cb = _load_module()


def _changed(_base: str, _head: str, _path: str) -> bool:
    return True


def _unchanged(_base: str, _head: str, _path: str) -> bool:
    return False


def test_baseline_unchanged_returns_zero() -> None:
    decision = cb.evaluate(  # type: ignore[attr-defined]
        body="",
        labels="",
        base_sha="abc",
        head_sha="def",
        baseline_path=".riskratchet.json",
        changed_fn=_unchanged,
    )
    assert decision.exit_code == 0
    assert "unchanged" in decision.message


def test_baseline_changed_with_rationale_heading_passes() -> None:
    body = textwrap.dedent(
        """
        Fixes some bug.

        ## Baseline bump rationale

        We deliberately accepted the new entries because the refactored
        helper has higher cyclomatic complexity but better coverage.
        """
    )
    decision = cb.evaluate(  # type: ignore[attr-defined]
        body=body,
        labels="",
        base_sha="abc",
        head_sha="def",
        baseline_path=".riskratchet.json",
        changed_fn=_changed,
    )
    assert decision.exit_code == 0
    assert "rationale accepted" in decision.message


def test_baseline_changed_with_heading_but_short_body_fails() -> None:
    body = textwrap.dedent(
        """
        ## Baseline bump rationale

        ok
        """
    )
    decision = cb.evaluate(  # type: ignore[attr-defined]
        body=body,
        labels="",
        base_sha="abc",
        head_sha="def",
        baseline_path=".riskratchet.json",
        changed_fn=_changed,
    )
    assert decision.exit_code == 1


def test_baseline_changed_with_inline_rationale_passes() -> None:
    body = (
        "riskratchet-baseline-rationale: refactored auth_middleware for "
        "compliance; complexity rose, coverage rose more."
    )
    decision = cb.evaluate(  # type: ignore[attr-defined]
        body=body,
        labels="",
        base_sha="abc",
        head_sha="def",
        baseline_path=".riskratchet.json",
        changed_fn=_changed,
    )
    assert decision.exit_code == 0


def test_baseline_changed_with_empty_body_fails() -> None:
    decision = cb.evaluate(  # type: ignore[attr-defined]
        body="",
        labels="",
        base_sha="abc",
        head_sha="def",
        baseline_path=".riskratchet.json",
        changed_fn=_changed,
    )
    assert decision.exit_code == 1
    assert "rationale" in decision.message.lower()


def test_baseline_changed_with_label_bypass_passes() -> None:
    decision = cb.evaluate(  # type: ignore[attr-defined]
        body="no rationale here",
        labels="bug,baseline-approved,priority:high",
        base_sha="abc",
        head_sha="def",
        baseline_path=".riskratchet.json",
        changed_fn=_changed,
    )
    assert decision.exit_code == 0
    assert "label" in decision.message.lower()


def test_baseline_changed_unrelated_label_does_not_bypass() -> None:
    decision = cb.evaluate(  # type: ignore[attr-defined]
        body="",
        labels="dependencies,baseline-related-but-not-the-bypass-label",
        base_sha="abc",
        head_sha="def",
        baseline_path=".riskratchet.json",
        changed_fn=_changed,
    )
    assert decision.exit_code == 1


def test_parse_rationale_accepts_multiline_body() -> None:
    body = textwrap.dedent(
        """
        ## Baseline bump rationale

        Line 1 of explanation.
        Line 2 of explanation provides additional context.
        """
    )
    rationale = cb.parse_rationale(body)  # type: ignore[attr-defined]
    assert rationale is not None
    assert "Line 1 of explanation." in rationale
    assert "Line 2 of explanation provides additional context." in rationale


def test_parse_rationale_returns_full_text_not_first_line() -> None:
    """Regression: heading-match path must return the entire rationale body,
    not just `splitlines()[0]`. The displayed gate message truncates for
    readability; the parsed text is the full content."""
    body = textwrap.dedent(
        """
        ## Baseline bump rationale

        First line of context.
        Second line with crucial detail that should not be silently dropped.
        Third line referencing a tracking ticket.
        """
    )
    rationale = cb.parse_rationale(body)  # type: ignore[attr-defined]
    assert rationale is not None
    assert rationale.count("\n") >= 2, "multi-line rationale must keep its line breaks"
    assert "Second line" in rationale
    assert "Third line" in rationale


def test_evaluate_message_truncates_long_rationale_for_display() -> None:
    """The gate's success message abbreviates to 80 chars even when the
    stored rationale is longer; tests pin the display contract."""
    long_body = textwrap.dedent(
        """
        ## Baseline bump rationale

        """
    ) + ("x" * 200)
    decision = cb.evaluate(  # type: ignore[attr-defined]
        body=long_body,
        labels="",
        base_sha="abc",
        head_sha="def",
        baseline_path=".riskratchet.json",
        changed_fn=_changed,
    )
    assert decision.exit_code == 0
    assert "rationale accepted" in decision.message
    assert len(decision.message) < 200, "message should abbreviate long rationales"


def test_parse_rationale_returns_none_for_unrelated_body() -> None:
    body = "## Summary\n\nUnrelated content."
    assert cb.parse_rationale(body) is None  # type: ignore[attr-defined]


def test_parse_rationale_rejects_inline_short_text() -> None:
    body = "riskratchet-baseline-rationale: ok"
    assert cb.parse_rationale(body) is None  # type: ignore[attr-defined]


@pytest.mark.skipif(
    subprocess.run(["which", "git"], capture_output=True).returncode != 0,
    reason="git not available",
)
def test_end_to_end_against_tmp_git_repo(tmp_path: Path) -> None:
    """Drive the actual script against a tmp repo with a baseline change."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env_base = {
        "GIT_AUTHOR_NAME": "T",
        "GIT_AUTHOR_EMAIL": "t@x",
        "GIT_COMMITTER_NAME": "T",
        "GIT_COMMITTER_EMAIL": "t@x",
    }
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True, env={**env_base})
    (repo / ".riskratchet.json").write_text('{"version":"2","entries":[]}\n', encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, env={**env_base})
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo, check=True, env={**env_base})
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    (repo / ".riskratchet.json").write_text(
        '{"version":"2","entries":[{"path":"a.py","qualname":"f","score":1.0,'
        '"components":{"coverage_gap":0,"structural_complexity":0,"branch_gap":0,'
        '"churn":0,"public_surface":0,"sprawl":0}}]}\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True, env={**env_base})
    subprocess.run(["git", "commit", "-q", "-m", "bump baseline"], cwd=repo, check=True, env={**env_base})
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    # No rationale → exit 1
    result = subprocess.run(
        ["python", str(SCRIPT)],
        cwd=repo,
        env={
            "BASE_SHA": base,
            "HEAD_SHA": head,
            "PR_BODY": "",
            "PR_LABELS": "",
            "PATH": __import__("os").environ.get("PATH", ""),
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stderr

    # With rationale heading + sufficient body → exit 0
    rationale_body = (
        "## Baseline bump rationale\n\n"
        "Intentionally accepted the new entries because the refactored helper has "
        "higher complexity but better coverage."
    )
    result_ok = subprocess.run(
        ["python", str(SCRIPT)],
        cwd=repo,
        env={
            "BASE_SHA": base,
            "HEAD_SHA": head,
            "PR_BODY": rationale_body,
            "PR_LABELS": "",
            "PATH": __import__("os").environ.get("PATH", ""),
        },
        capture_output=True,
        text=True,
    )
    assert result_ok.returncode == 0, result_ok.stderr

    # With commit-token bypass on the HEAD commit
    subprocess.run(
        ["git", "commit", "--allow-empty", "-q", "-m", "more changes [riskratchet-baseline-bypass]"],
        cwd=repo,
        check=True,
        env={**env_base},
    )
    head2 = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    result_token = subprocess.run(
        ["python", str(SCRIPT)],
        cwd=repo,
        env={
            "BASE_SHA": base,
            "HEAD_SHA": head2,
            "PR_BODY": "",
            "PR_LABELS": "",
            "PATH": __import__("os").environ.get("PATH", ""),
        },
        capture_output=True,
        text=True,
    )
    assert result_token.returncode == 0, result_token.stderr


def test_custom_baseline_path_via_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """RR_BASELINE_PATH env var overrides the default '.riskratchet.json'."""

    def fake_changed(_base: str, _head: str, path: str) -> bool:
        return path == "custom-baseline.json"

    decision = cb.evaluate(  # type: ignore[attr-defined]
        body="",
        labels="",
        base_sha="abc",
        head_sha="def",
        baseline_path="custom-baseline.json",
        changed_fn=fake_changed,
    )
    assert decision.exit_code == 1
    decision_unchanged = cb.evaluate(  # type: ignore[attr-defined]
        body="",
        labels="",
        base_sha="abc",
        head_sha="def",
        baseline_path=".riskratchet.json",
        changed_fn=fake_changed,
    )
    assert decision_unchanged.exit_code == 0
