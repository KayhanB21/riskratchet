"""Pure-parser unit tests for SZZ hunk + blame parsing (no git needed)."""

from __future__ import annotations

from bin.calibration.szz import BlameLine, _parse_blame_porcelain, _parse_old_hunks


def test_old_hunk_basic_range() -> None:
    diff = "--- a/m.py\n+++ b/m.py\n@@ -10,3 +10,5 @@\n"
    assert _parse_old_hunks(diff) == [("m.py", 10, 12)]


def test_old_hunk_single_line_no_count() -> None:
    diff = "--- a/m.py\n+++ b/m.py\n@@ -7 +7 @@\n"
    assert _parse_old_hunks(diff) == [("m.py", 7, 7)]


def test_old_hunk_skips_pure_addition() -> None:
    # old_len == 0 => nothing was deleted, nothing to blame.
    diff = "--- a/m.py\n+++ b/m.py\n@@ -0,0 +1,4 @@\n"
    assert _parse_old_hunks(diff) == []


def test_old_hunk_added_file_dev_null() -> None:
    diff = "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,3 @@\n"
    assert _parse_old_hunks(diff) == []


def test_old_hunk_multi_file_tracks_old_path() -> None:
    diff = (
        "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n"
        "diff --git a/y.py b/y.py\n--- a/y.py\n+++ b/y.py\n@@ -5 +5 @@\n"
    )
    assert _parse_old_hunks(diff) == [("x.py", 1, 2), ("y.py", 5, 5)]


def test_old_hunk_strips_quotes() -> None:
    diff = '--- "a/with space.py"\n+++ "b/with space.py"\n@@ -3,1 +3,1 @@\n'
    assert _parse_old_hunks(diff) == [("with space.py", 3, 3)]


def test_blame_porcelain_headers() -> None:
    sha_a = "a" * 40
    sha_b = "b" * 40
    text = f"{sha_a} 12 12 1\nauthor Alice\n\tbuggy_line()\n{sha_b} 20 13\nauthor Bob\n\tother_line()\n"
    assert _parse_blame_porcelain(text) == [
        BlameLine(introducer_sha=sha_a, lineno_at_parent=12),
        BlameLine(introducer_sha=sha_b, lineno_at_parent=13),
    ]


def test_blame_porcelain_ignores_previous_and_metadata() -> None:
    sha = "c" * 40
    text = f"{sha} 1 1 1\nprevious {'d' * 40} m.py\nfilename m.py\n\tcode\n"
    assert _parse_blame_porcelain(text) == [BlameLine(introducer_sha=sha, lineno_at_parent=1)]
