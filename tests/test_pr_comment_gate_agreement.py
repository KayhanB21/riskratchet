"""The PR comment must never contradict the exit code beside it.

`action.yml` runs `check --format pr-comment`, upserts the body as the sticky
PR comment, and then prints `::error::riskratchet exited $status — see PR
comment for details.` That makes the comment the *only* place a reviewer reads
why the job failed, so a body that disagrees with the exit code is worse than
no comment at all.

In baseline mode the comment used to be selected by `DiffStatus`, while the
gate is `regressions_from_diff`, which keys on `RegressionKind`. The two sets
are not the same set and each contains members the other cannot:

* `EXISTING_ABOVE_THRESHOLD` fires on entries whose status is `UNCHANGED`,
  `IMPROVED`, or `MOVED` — none of which were rendered. Exit 1, comment read
  "_No risk regressions detected._"
* A `NEW` entry at or below `fail_new_above` trips no gate but *was* rendered
  as a visible row. Exit 0, comment showed a regression.

These tests sweep every `RegressionKind` through the real CLI and assert the
body and the exit code agree in both directions.
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent

import pytest
from typer.testing import CliRunner

from riskratchet.cli import app
from riskratchet.models import RegressionKind

runner = CliRunner()

# The visible table sits between the summary lines and the first collapsed
# `<details>` block, which holds diff context rather than gate findings.
_CONTEXT_MARKER = "<details>"

_NO_REGRESSIONS = "_No risk regressions detected._"


def _visible(body: str) -> str:
    """The part of the comment a reviewer sees without expanding anything."""
    return body.split(_CONTEXT_MARKER, 1)[0]


def _project(tmp_path: Path, *, extra: str = "") -> Path:
    # An explicit empty config keeps discovery from walking up into
    # riskratchet's own `pyproject.toml` and baseline.
    (tmp_path / "pyproject.toml").write_text("[tool.riskratchet]\n", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / "m.py").write_text(
        dedent(
            """
            def trivial():
                return 1

            def branchy(x, y, z):
                if x > 0:
                    return 1
                if y < 0:
                    return -1
                if z:
                    return 2
                return 0
            """
        ).strip()
        + "\n"
        + extra,
        encoding="utf-8",
    )
    return src


def _run(args: list[str], *, config: Path) -> tuple[int, str]:
    result = runner.invoke(
        app,
        [*args, "--config", str(config), "--allow-missing-coverage", "--no-auto-cov", "--no-git"],
    )
    return result.exit_code, result.stdout


def _baseline(src: Path, path: Path) -> None:
    cfg = src.parent / "pyproject.toml"
    code, out = _run(["baseline", str(src), "--output", str(path)], config=cfg)
    assert code == 0, out


@pytest.mark.parametrize(
    ("kind", "check_args", "extra_source"),
    [
        # Fires on entries the diff calls UNCHANGED — invisible before the fix.
        (RegressionKind.EXISTING_ABOVE_THRESHOLD, ["--fail-existing-above", "1"], ""),
        # Fires with no baseline at all (`--fail-above` mode).
        (RegressionKind.ABOVE_THRESHOLD, ["--fail-above", "1"], ""),
        (
            RegressionKind.NEW_ABOVE_THRESHOLD,
            ["--fail-new-above", "1"],
            "\n\ndef added(a, b):\n    return a if a > b else b\n",
        ),
    ],
)
def test_a_tripped_gate_never_reads_as_clean(
    tmp_path: Path,
    kind: RegressionKind,
    check_args: list[str],
    extra_source: str,
) -> None:
    src = _project(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    _baseline(src, baseline_path)
    if extra_source:
        _project(tmp_path, extra=extra_source)

    args = ["check", str(src), "--format", "pr-comment", *check_args]
    if "--fail-above" not in check_args:
        args += ["--baseline", str(baseline_path)]
    code, body = _run(args, config=tmp_path / "pyproject.toml")

    assert code == 1, f"{kind.value} was expected to trip the gate:\n{body}"
    assert _NO_REGRESSIONS not in _visible(body), (
        f"{kind.value} tripped the gate (exit 1) but the comment reads as clean:\n{body}"
    )
    assert f"| {kind.value} |" in _visible(body), (
        f"{kind.value} tripped the gate but is not a visible row:\n{body}"
    )


def test_a_regressed_function_is_a_visible_row(tmp_path: Path) -> None:
    src = _project(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    _baseline(src, baseline_path)
    _project(
        tmp_path,
        extra="",
    )
    # Grow `branchy` well past its baseline score.
    (src / "m.py").write_text(
        "def trivial():\n    return 1\n\n\n"
        "def branchy(a, b, c, d, e, f, g):\n"
        + "".join(f"    if {n}:\n        return {i}\n" for i, n in enumerate("abcdefg"))
        + "    return 0\n",
        encoding="utf-8",
    )
    code, body = _run(
        [
            "check",
            str(src),
            "--baseline",
            str(baseline_path),
            "--fail-regression-above",
            "1",
            "--format",
            "pr-comment",
        ],
        config=tmp_path / "pyproject.toml",
    )
    assert code == 1, body
    assert _NO_REGRESSIONS not in _visible(body), body
    assert f"| {RegressionKind.REGRESSED.value} |" in _visible(body), body


def test_a_component_regression_is_a_visible_row(tmp_path: Path) -> None:
    """The kind that only exists because the total can hide a component."""
    src = _project(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    _baseline(src, baseline_path)

    # Drop one component far below what the current code scores while keeping
    # the recorded total high, so the gate can only see it component-wise.
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    for entry in data["entries"]:
        if entry["qualname"] == "branchy":
            entry["components"]["coverage_gap"] = 0.0
            entry["score"] = 99.0
    baseline_path.write_text(json.dumps(data), encoding="utf-8")

    code, body = _run(
        [
            "check",
            str(src),
            "--baseline",
            str(baseline_path),
            "--format",
            "pr-comment",
        ],
        config=tmp_path / "pyproject.toml",
    )
    assert code == 1, body
    assert _NO_REGRESSIONS not in _visible(body), body
    assert f"| {RegressionKind.COMPONENT_REGRESSED.value} |" in _visible(body), body


def test_the_matrix_covers_every_regression_kind() -> None:
    """A new `RegressionKind` must not slip in without a comment assertion."""
    covered = {
        RegressionKind.EXISTING_ABOVE_THRESHOLD,
        RegressionKind.ABOVE_THRESHOLD,
        RegressionKind.NEW_ABOVE_THRESHOLD,
        RegressionKind.REGRESSED,
        RegressionKind.COMPONENT_REGRESSED,
    }
    assert covered == set(RegressionKind)


def test_a_passing_gate_shows_no_visible_finding(tmp_path: Path) -> None:
    """A new function below `fail_new_above` is context, not a finding."""
    src = _project(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    _baseline(src, baseline_path)
    _project(tmp_path, extra="\n\ndef added(a, b):\n    return a if a > b else b\n")

    code, body = _run(
        [
            "check",
            str(src),
            "--baseline",
            str(baseline_path),
            "--fail-new-above",
            "99",
            "--format",
            "pr-comment",
        ],
        config=tmp_path / "pyproject.toml",
    )

    assert code == 0, body
    assert _NO_REGRESSIONS in _visible(body), f"gate passed (exit 0) but the comment shows a finding:\n{body}"
    # The diff is still reported, just as context rather than as a verdict.
    assert "<summary>New functions (1)</summary>" in body


def test_the_diff_context_never_restates_the_gate_label(tmp_path: Path) -> None:
    """Both summary lines counted different things under one `Regressions:` label."""
    src = _project(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    _baseline(src, baseline_path)

    code, body = _run(
        [
            "check",
            str(src),
            "--baseline",
            str(baseline_path),
            "--fail-existing-above",
            "1",
            "--format",
            "pr-comment",
        ],
        config=tmp_path / "pyproject.toml",
    )

    assert code == 1, body
    assert body.count("**Regressions:**") == 1, f"two lines claim to count regressions and disagree:\n{body}"
    assert "_Since the baseline:_" in body


def test_every_gated_function_appears_exactly_once(tmp_path: Path) -> None:
    """A gated entry must not be listed again in the diff context sections."""
    src = _project(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    _baseline(src, baseline_path)

    code, body = _run(
        [
            "check",
            str(src),
            "--baseline",
            str(baseline_path),
            "--fail-existing-above",
            "1",
            "--format",
            "pr-comment",
        ],
        config=tmp_path / "pyproject.toml",
    )

    assert code == 1, body
    assert body.count("src/m.py::branchy") == 1, f"a gated function is listed twice:\n{body}"
