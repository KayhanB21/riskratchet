"""Change-counting on a real (local, networkless) git repo + pure-parser unit tests.

Plants a function, edits a line inside it across several commits, and asserts those edits
are attributed to the right snapshot-`S` function (and that an untouched function is not).
git is available in CI; no network, no gh, no clone.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from bin.calibration.change_counting import (
    _parse_new_hunks,
    commits_in_range,
    count_changes,
    past_window_start,
)
from bin.calibration.corpus import analyze_report
from bin.calibration.defects import SnapshotPopulation

from riskratchet.models import FunctionId

_V0 = """\
def helper():
    return 1


def parse(text):
    value = text.strip()
    if value == "A":
        return 1
    if value == "B":
        return 2
    return 0
"""


def _edit(version: str, old: str, new: str) -> str:
    assert old in version
    return version.replace(old, new)


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=root, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=root, check=True)


def _commit(root: Path, relative: str, body: str, message: str) -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    subprocess.run(["git", "add", relative], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=root, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_parse_new_hunks() -> None:
    diff = (
        "diff --git a/m.py b/m.py\n"
        "--- a/m.py\n"
        "+++ b/m.py\n"
        "@@ -7 +7 @@\n"
        "-        return 1\n"
        "+        return 10\n"
        "@@ -10,0 +11,2 @@\n"
        "+    # added\n"
        "+    pass\n"
    )
    assert _parse_new_hunks(diff) == [("m.py", 7, 7), ("m.py", 11, 12)]


def test_parse_new_hunks_skips_pure_deletion() -> None:
    diff = "--- a/m.py\n+++ b/m.py\n@@ -5,2 +4,0 @@\n-gone\n-also gone\n"
    assert _parse_new_hunks(diff) == []


def test_count_changes_attributes_edits_to_the_edited_function(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    sha_s = _commit(tmp_path, "m.py", _V0, "feat: add parse")
    v1 = _edit(_V0, "        return 1\n", "        return 10\n")
    _commit(tmp_path, "m.py", v1, "tweak A branch")
    v2 = _edit(v1, "        return 2\n", "        return 20\n")
    _commit(tmp_path, "m.py", v2, "tweak B branch")
    v3 = _edit(v2, "    return 0\n", "    return 99\n")
    _commit(tmp_path, "m.py", v3, "tweak default")

    # Snapshot population at S: parse + helper both exist; tracking is exact-id here.
    report = analyze_report([tmp_path], tmp_path)
    snapshot = SnapshotPopulation(snapshot_sha=sha_s, report=report)
    commits = commits_in_range(tmp_path, sha_s, "HEAD", ("m.py",))
    assert len(commits) == 3

    counts = count_changes(tmp_path, snapshot, commits, ("m.py",))

    parse_id = FunctionId("m.py", "parse")
    assert parse_id in counts
    commit_count, lines = counts[parse_id]
    assert commit_count == 3  # touched in all three future commits
    assert lines >= 3
    # `helper` was never edited.
    assert FunctionId("m.py", "helper") not in counts


def test_past_window_start_none_when_repo_too_young(tmp_path: Path) -> None:
    # A repo seconds old has no commit a year before HEAD => past window is unavailable.
    _init_repo(tmp_path)
    head = _commit(tmp_path, "m.py", _V0, "feat: add parse")
    assert past_window_start(tmp_path, head, 365) is None
