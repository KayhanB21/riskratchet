"""Load + validate the calibration corpus and PR-label config.

Two checked-in TOML files drive the harness:

- ``data/calibration/corpus.toml`` — the repos to replay, with their per-revision
  coverage recipe.
- ``data/calibration/pr-labels.toml`` — hand-authored accepted/rejected outcome
  labels, pinned to the exact base/head SHAs that were replayed.

Validation is deliberately lenient on unknown keys (warn, don't fail — mirrors
riskratchet's own config walk) but strict on the keys the harness depends on.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib  # type: ignore[import-not-found]

from bin.calibration.corpus import CALIBRATION_DIR

REPOS_DIR = CALIBRATION_DIR / "repos"
LABELS_PATH = CALIBRATION_DIR / "pr-labels.toml"

COVERAGE_PLACEHOLDER = "{coverage_out}"
_VALID_LABELS = frozenset({"accepted", "rejected"})

_REPO_REQUIRED = ("name", "url", "test_command", "coverage_prefix")
_REPO_KNOWN = frozenset(
    {
        "name",
        "url",
        "paths",
        "pr_branch",
        "python",
        "extras",
        "test_command",
        "coverage_prefix",
        "replay_enabled",
        "timeouts",
        # phase-2 SZZ defect-linking (all optional)
        "snapshot_sha",
        "snapshot_days",
        "defect_window_days",
        "fix_keywords",
        "ignore_revs_file",
        "test_requirements",
        "test_deps",
    }
)
_TIMEOUTS_KNOWN = frozenset({"install_seconds", "test_seconds"})
_LABEL_REQUIRED = ("repo", "pr", "base_sha", "head_sha", "label")
_LABEL_KNOWN = frozenset({"repo", "pr", "base_sha", "head_sha", "label", "note"})


class ConfigError(ValueError):
    """A corpus or label entry is missing a required key or malformed."""


@dataclass(frozen=True)
class Timeouts:
    install_seconds: int = 600
    test_seconds: int = 1200


@dataclass(frozen=True)
class RepoConfig:
    name: str
    url: str
    test_command: str
    coverage_prefix: str
    paths: tuple[str, ...] = ()
    pr_branch: str = "main"
    python: str = "3.12"
    extras: tuple[str, ...] = ()
    replay_enabled: bool = False
    timeouts: Timeouts = field(default_factory=Timeouts)
    # phase-2 SZZ defect-linking. snapshot_sha pins S; otherwise it is derived
    # from snapshot_days. fix_keywords empty => harness default. ignore_revs_file
    # is a repo-relative path of mass-reformat SHAs to skip during blame.
    snapshot_sha: str = ""
    snapshot_days: int = 365
    defect_window_days: int = 365
    fix_keywords: tuple[str, ...] = ()
    ignore_revs_file: str = ""
    # Test-only deps the suite needs that `.[extras]` doesn't cover: a repo-relative
    # requirements file and/or an explicit package list (installed into the venv).
    test_requirements: str = ""
    test_deps: tuple[str, ...] = ()


@dataclass(frozen=True)
class PrLabel:
    repo: str
    pr: int
    base_sha: str
    head_sha: str
    label: str
    note: str = ""

    @property
    def key(self) -> tuple[str, int, str, str]:
        """Join key against a replayed PR record: (repo, pr, base_sha, head_sha)."""
        return (self.repo, self.pr, self.base_sha, self.head_sha)


def _warn_unknown(kind: str, ident: str, keys: frozenset[str], known: frozenset[str]) -> None:
    extra = sorted(keys - known)
    if extra:
        print(f"warning: {kind} {ident!r} has unknown keys: {', '.join(extra)}", file=sys.stderr)


def _require(table: dict[str, object], required: tuple[str, ...], kind: str, ident: str) -> None:
    missing = [k for k in required if k not in table]
    if missing:
        raise ConfigError(f"{kind} {ident!r} is missing required keys: {', '.join(missing)}")


def _parse_repo(table: dict[str, object]) -> RepoConfig:
    ident = str(table.get("name", "<unnamed>"))
    _require(table, _REPO_REQUIRED, "repo", ident)
    _warn_unknown("repo", ident, frozenset(table), _REPO_KNOWN)

    test_command = str(table["test_command"])
    _validate_test_command(test_command, ident)

    timeouts_raw = table.get("timeouts", {})
    if not isinstance(timeouts_raw, dict):
        raise ConfigError(f"repo {ident!r}: [repo.timeouts] must be a table")
    _warn_unknown("repo.timeouts", ident, frozenset(timeouts_raw), _TIMEOUTS_KNOWN)
    timeouts = Timeouts(
        install_seconds=int(timeouts_raw.get("install_seconds", Timeouts.install_seconds)),
        test_seconds=int(timeouts_raw.get("test_seconds", Timeouts.test_seconds)),
    )

    return RepoConfig(
        name=str(table["name"]),
        url=str(table["url"]),
        test_command=test_command,
        coverage_prefix=str(table["coverage_prefix"]),
        paths=tuple(str(p) for p in _as_list(table.get("paths", []), ident, "paths")),
        pr_branch=str(table.get("pr_branch", "main")),
        python=str(table.get("python", "3.12")),
        extras=tuple(str(e) for e in _as_list(table.get("extras", []), ident, "extras")),
        replay_enabled=bool(table.get("replay_enabled", False)),
        timeouts=timeouts,
        snapshot_sha=str(table.get("snapshot_sha", "")),
        snapshot_days=_int_field(table, "snapshot_days", 365, ident),
        defect_window_days=_int_field(table, "defect_window_days", 365, ident),
        fix_keywords=tuple(str(k) for k in _as_list(table.get("fix_keywords", []), ident, "fix_keywords")),
        ignore_revs_file=str(table.get("ignore_revs_file", "")),
        test_requirements=str(table.get("test_requirements", "")),
        test_deps=tuple(str(d) for d in _as_list(table.get("test_deps", []), ident, "test_deps")),
    )


def _validate_test_command(command: str, ident: str) -> None:
    if COVERAGE_PLACEHOLDER not in command:
        raise ConfigError(f"repo {ident!r}: test_command must contain {COVERAGE_PLACEHOLDER}")
    # Reject any other {placeholder} so a typo can't silently produce a bad path.
    stripped = command.replace(COVERAGE_PLACEHOLDER, "")
    if "{" in stripped or "}" in stripped:
        raise ConfigError(
            f"repo {ident!r}: test_command has an unexpected placeholder; only "
            f"{COVERAGE_PLACEHOLDER} is allowed"
        )


def _as_list(value: object, ident: str, key: str) -> list[object]:
    if not isinstance(value, list):
        raise ConfigError(f"repo {ident!r}: {key} must be an array")
    return value


def _int_field(table: dict[str, object], key: str, default: int, ident: str) -> int:
    value = table.get(key, default)
    if not isinstance(value, int):
        raise ConfigError(f"repo {ident!r}: {key} must be an integer")
    return value


def _parse_label(table: dict[str, object]) -> PrLabel:
    ident = f"{table.get('repo', '?')}#{table.get('pr', '?')}"
    _require(table, _LABEL_REQUIRED, "label", ident)
    _warn_unknown("label", ident, frozenset(table), _LABEL_KNOWN)
    label = str(table["label"])
    if label not in _VALID_LABELS:
        raise ConfigError(f"label {ident!r}: label must be one of {sorted(_VALID_LABELS)}, got {label!r}")
    pr_raw = table["pr"]
    if not isinstance(pr_raw, int):
        raise ConfigError(f"label {ident!r}: pr must be an integer")
    return PrLabel(
        repo=str(table["repo"]),
        pr=pr_raw,
        base_sha=str(table["base_sha"]),
        head_sha=str(table["head_sha"]),
        label=label,
        note=str(table.get("note", "")),
    )


def load_repo(path: Path) -> RepoConfig:
    """Parse a single per-repo ``repo.toml`` (top-level keys = the repo table)."""
    return _parse_repo(_load_toml(path))


def load_corpus(repos_dir: Path | None = None) -> list[RepoConfig]:
    """Load every ``repos/<name>/repo.toml`` under the corpus, sorted by name."""
    root = repos_dir or REPOS_DIR
    repos = [load_repo(p) for p in sorted(root.glob("*/repo.toml"))]
    return sorted(repos, key=lambda r: r.name)


def load_labels(path: Path | None = None) -> list[PrLabel]:
    """Parse ``pr-labels.toml`` into validated ``PrLabel`` entries (may be empty)."""
    target = path or LABELS_PATH
    if not target.exists():
        return []
    data = _load_toml(target)
    labels_raw = data.get("label", [])
    if not isinstance(labels_raw, list):
        raise ConfigError("pr-labels.toml: [[label]] must be an array of tables")
    return [_parse_label(label) for label in labels_raw]


def _load_toml(path: Path) -> dict[str, object]:
    parsed: dict[str, object] = tomllib.loads(path.read_text(encoding="utf-8"))
    return parsed


def parse_corpus_text(text: str) -> list[RepoConfig]:
    """Parse corpus TOML from a string (test convenience)."""
    raw = tomllib.loads(text).get("repo", [])
    if not isinstance(raw, list):
        raise ConfigError("[[repo]] must be an array of tables")
    return [_parse_repo(r) for r in raw]


def parse_labels_text(text: str) -> list[PrLabel]:
    """Parse PR-label TOML from a string (test convenience)."""
    raw = tomllib.loads(text).get("label", [])
    if not isinstance(raw, list):
        raise ConfigError("[[label]] must be an array of tables")
    return [_parse_label(label) for label in raw]
