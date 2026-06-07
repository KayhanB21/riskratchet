"""Review-comment construct anchor: classify, parse, map-to-function, agreement.

The mapping test uses a real local git repo; `gh` output is injected as JSON. No network.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from bin.calibration.corpus import analyze_report
from bin.calibration.defects import SnapshotPopulation
from bin.calibration.proneness import build_proneness_labels
from bin.calibration.review_comments import (
    ReviewComment,
    ReviewFlags,
    flag_agreement_auc,
    flags_to_dict,
    is_maintainability_comment,
    map_comment_to_function,
    parse_review_comments,
)

from riskratchet.models import FunctionId

_SRC = """\
def helper():
    return 1


def parse(text):
    value = text.strip()
    if value == "A":
        return 1
    return 0
"""


def test_classification() -> None:
    assert is_maintainability_comment("Can you split this up? It's doing too much.")
    assert is_maintainability_comment("this function is too long")
    assert is_maintainability_comment("Please extract a helper here")
    assert not is_maintainability_comment("LGTM, nice work")
    assert not is_maintainability_comment("split view looks great")  # not about the code


def test_parse_review_comments_uses_line_or_original_line() -> None:
    stdout = json.dumps(
        [
            {"path": "m.py", "line": 7, "body": "split this", "commit_id": "abc"},
            {"path": "m.py", "original_line": 9, "body": "ok", "original_commit_id": "def"},
            {"path": "m.py", "body": "no line -> skipped", "commit_id": "ghi"},
        ]
    )
    comments = parse_review_comments(stdout)
    assert [(c.line, c.commit_id) for c in comments] == [(7, "abc"), (9, "def")]


def _init_repo(root: Path) -> str:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=root, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=root, check=True)
    (root / "m.py").write_text(_SRC, encoding="utf-8")
    subprocess.run(["git", "add", "m.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_map_comment_to_function(tmp_path: Path) -> None:
    sha = _init_repo(tmp_path)
    snapshot = SnapshotPopulation(snapshot_sha=sha, report=analyze_report([tmp_path], tmp_path))
    # Line 7 sits inside `parse`.
    comment = ReviewComment(path="m.py", line=7, body="please split this", commit_id=sha)

    assert map_comment_to_function(tmp_path, comment, snapshot) == FunctionId("m.py", "parse")


def test_flag_agreement_auc(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.py").write_text(
        "".join(f"def f{i}(x):\n    return x\n\n\n" for i in range(6)), encoding="utf-8"
    )
    snapshot = SnapshotPopulation(snapshot_sha="S" * 40, report=analyze_report([src], tmp_path))
    fid = FunctionId  # alias
    future = {fid("src/m.py", "f5"): (8, 0), fid("src/m.py", "f4"): (6, 0), fid("src/m.py", "f0"): (1, 0)}
    labels = build_proneness_labels(
        "demo",
        snapshot,
        future,
        {},
        head_sha="H" * 40,
        window_days=365,
        n_future_commits=15,
        insufficient_past_history=False,
    )
    # Humans flagged exactly the two most change-prone functions => perfect agreement.
    flags = ReviewFlags(
        "demo", "S" * 40, 5, 20, 2, counts={fid("src/m.py", "f5"): 1, fid("src/m.py", "f4"): 1}
    )

    assert flag_agreement_auc(flags, labels) == 1.0


def test_flags_to_dict_shape() -> None:
    flags = ReviewFlags("demo", "S" * 40, 3, 10, 2, counts={FunctionId("m.py", "g"): 2})
    payload = flags_to_dict(flags)
    assert payload["n_flagged_functions"] == 1
    assert payload["functions"] == [{"target": "m.py::g", "flags": 2}]
