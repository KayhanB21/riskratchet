"""Tests for the calibration corpus + PR-label config loader."""

from __future__ import annotations

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
    """Every checked-in repos/<name>/repo.toml parses and has the expected shape."""
    repos = load_corpus()
    names = {r.name for r in repos}
    assert {
        "requests",
        "httpx",
        "rich",
        "fastapi",
        "cassandra-python-driver",
        "click",
        "jinja2",
        "sqlglot",
        "arrow",
        "jsonschema",
        "flask",
        "werkzeug",
        "pygments",
        "markdown",
    } == names
    enabled = {r.name for r in repos if r.replay_enabled}
    # Repos whose suites run under the replay budget and yield a defect snapshot.
    assert enabled == {
        "requests",
        "rich",
        "click",
        "sqlglot",
        "arrow",
        "jsonschema",
        "flask",
        "werkzeug",
        "pygments",
        "markdown",
    }
    for repo in repos:
        assert config.COVERAGE_PLACEHOLDER in repo.test_command


def test_shipped_labels_config_is_valid() -> None:
    """The seed pr-labels.toml parses (currently empty / all-commented)."""
    assert load_labels() == []
