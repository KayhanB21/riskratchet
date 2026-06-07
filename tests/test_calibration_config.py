"""Tests for the calibration corpus + PR-label config loader."""

from __future__ import annotations

import json

import pytest
from bin.calibration import config
from bin.calibration.config import (
    ConfigError,
    load_corpus,
    load_labels,
    parse_corpus_text,
    parse_labels_text,
)

_MIN_REPO = """
[[repo]]
name = "demo"
url = "https://example.com/demo"
test_command = "pytest -q --cov-report=json:{coverage_out}"
coverage_prefix = "demo"
"""


def test_minimal_repo_defaults() -> None:
    (repo,) = parse_corpus_text(_MIN_REPO)
    assert repo.name == "demo"
    assert repo.paths == ()
    assert repo.pr_branch == "main"
    assert repo.python == "3.12"
    assert repo.extras == ()
    assert repo.replay_enabled is False
    assert repo.timeouts.install_seconds == 600
    assert repo.timeouts.test_seconds == 1200


def test_full_repo_fields() -> None:
    text = """
    [[repo]]
    name = "rich"
    url = "https://github.com/Textualize/rich"
    paths = ["rich"]
    pr_branch = "master"
    python = "3.11"
    extras = ["jupyter"]
    test_command = "pytest tests -q --cov=rich --cov-report=json:{coverage_out}"
    coverage_prefix = "rich"
    replay_enabled = true
    [repo.timeouts]
    install_seconds = 300
    test_seconds = 900
    """
    (repo,) = parse_corpus_text(text)
    assert repo.paths == ("rich",)
    assert repo.pr_branch == "master"
    assert repo.python == "3.11"
    assert repo.extras == ("jupyter",)
    assert repo.replay_enabled is True
    assert repo.timeouts == config.Timeouts(install_seconds=300, test_seconds=900)


def test_missing_required_key_raises() -> None:
    with pytest.raises(ConfigError, match="missing required keys: coverage_prefix"):
        parse_corpus_text(
            """
            [[repo]]
            name = "x"
            url = "u"
            test_command = "pytest --cov-report=json:{coverage_out}"
            """
        )


def test_test_command_must_have_placeholder() -> None:
    with pytest.raises(ConfigError, match="must contain"):
        parse_corpus_text(
            """
            [[repo]]
            name = "x"
            url = "u"
            test_command = "pytest -q"
            coverage_prefix = "x"
            """
        )


def test_test_command_rejects_other_placeholders() -> None:
    with pytest.raises(ConfigError, match="unexpected placeholder"):
        parse_corpus_text(
            """
            [[repo]]
            name = "x"
            url = "u"
            test_command = "pytest {oops} --cov-report=json:{coverage_out}"
            coverage_prefix = "x"
            """
        )


def test_unknown_repo_key_warns_not_fails(capsys: pytest.CaptureFixture[str]) -> None:
    (repo,) = parse_corpus_text(
        """
        [[repo]]
        name = "x"
        url = "u"
        test_command = "pytest --cov-report=json:{coverage_out}"
        coverage_prefix = "x"
        bogus_key = 1
        """
    )
    assert repo.name == "x"
    assert "unknown keys: bogus_key" in capsys.readouterr().err


def test_coverage_free_repo_needs_no_test_command() -> None:
    (repo,) = parse_corpus_text(
        """
        [[repo]]
        name = "tiny-tool"
        url = "https://github.com/x/tiny-tool"
        paths = ["tiny_tool"]
        pr_branch = "main"
        snapshot_sha = "deadbeef"
        coverage_free = true
        """
    )
    assert repo.coverage_free is True
    assert repo.test_command == ""  # not required for coverage-free repos
    assert repo.coverage_prefix == ""
    assert repo.paths == ("tiny_tool",)


def test_non_coverage_free_still_requires_test_command() -> None:
    with pytest.raises(ConfigError, match="missing required keys: test_command"):
        parse_corpus_text(
            """
            [[repo]]
            name = "x"
            url = "u"
            coverage_prefix = "x"
            """
        )


def test_label_parsing_and_key() -> None:
    (label,) = parse_labels_text(
        """
        [[label]]
        repo = "requests"
        pr = 42
        base_sha = "aaaa"
        head_sha = "bbbb"
        label = "accepted"
        note = "fine"
        """
    )
    assert label.key == ("requests", 42, "aaaa", "bbbb")
    assert label.label == "accepted"
    assert label.note == "fine"


def test_label_rejects_unknown_value() -> None:
    with pytest.raises(ConfigError, match="must be one of"):
        parse_labels_text(
            """
            [[label]]
            repo = "r"
            pr = 1
            base_sha = "a"
            head_sha = "b"
            label = "maybe"
            """
        )


def test_load_labels_missing_file_is_empty(tmp_path: object) -> None:
    from pathlib import Path

    assert load_labels(Path(str(tmp_path)) / "nope.toml") == []


def test_shipped_corpus_config_is_valid() -> None:
    """Every checked-in repos/<name>/repo.toml parses and obeys the corpus invariants.

    The corpus is large (50+ tried repos, ~34 enabled) and grows, so we assert
    structural invariants rather than a hardcoded name list:
      * every config parses and carries the coverage placeholder + a pinned snapshot;
      * names are unique;
      * a healthy number of repos are enabled;
      * every *enabled* repo has a committed, non-empty defect-labels.json (the
        contract: enabled == yields a usable defect snapshot), and every *disabled*
        repo does not (dormant/un-harnessable repos are kept as recipe records only).
    """

    repos = load_corpus()
    names = [r.name for r in repos]
    assert len(names) == len(set(names)), "duplicate repo names"

    enabled = [r for r in repos if r.replay_enabled]
    assert len(enabled) >= 30, f"expected >=30 enabled repos, got {len(enabled)}"

    for repo in repos:
        # Coverage-free (phase-4 gradient) repos carry no test recipe.
        if not repo.coverage_free:
            assert config.COVERAGE_PLACEHOLDER in repo.test_command
        labels = config.REPOS_DIR / repo.name / "defect-labels.json"
        if repo.replay_enabled:
            assert repo.snapshot_sha, f"{repo.name}: enabled repos must pin a snapshot"
            assert labels.exists(), f"{repo.name}: enabled but no defect-labels.json"
            data = json.loads(labels.read_text())
            assert data["n_defect_functions"] > 0, f"{repo.name}: enabled but 0 defect functions"


def test_shipped_labels_config_is_valid() -> None:
    """The seed pr-labels.toml parses (currently empty / all-commented)."""
    assert load_labels() == []
