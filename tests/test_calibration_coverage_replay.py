"""Tests for the coverage-replay orchestration + analyze-cache serialization.

Hermetic: the injectable CommandRunner is replaced with a fake, so no real
clone/venv/pytest ever runs. The cache dirs are redirected into tmp_path.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from bin.calibration import coverage_replay
from bin.calibration.config import RepoConfig
from bin.calibration.corpus import analyze_report
from bin.calibration.coverage_replay import replay_revision, revision_cache_dir
from bin.calibration.serial import report_from_dict, report_to_dict

_FN = "def f(x):\n    if x > 0:\n        return x\n    return -x\n"


def _repo() -> RepoConfig:
    return RepoConfig(
        name="demo",
        url="https://github.com/x/demo",
        test_command="pytest -q --cov-report=json:{coverage_out}",
        coverage_prefix="demo",
        paths=("src",),
    )


def _project(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "m.py").write_text(_FN, encoding="utf-8")
    return tmp_path


# --- serialization round-trip --------------------------------------------


def test_serial_round_trip(tmp_path: Path) -> None:
    root = _project(tmp_path)
    report = analyze_report([root / "src"], root)
    restored = report_from_dict(report_to_dict(report))
    # Re-serializing the restored report yields identical JSON.
    assert report_to_dict(restored) == report_to_dict(report)
    assert restored.functions[0].id.as_target() == "src/m.py::f"
    assert restored.functions[0].components.sprawl == report.functions[0].components.sprawl


# --- skip / resume -------------------------------------------------------


def test_replay_revision_short_circuits_on_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(coverage_replay, "CACHE_DIR", tmp_path / "_cache")
    repo = _repo()
    sha = "a" * 40

    # Pre-populate the cache exactly as a prior run would have.
    proj = _project(tmp_path / "proj")
    report = analyze_report([proj / "src"], proj)
    cache = revision_cache_dir(repo.name, sha)
    cache.mkdir(parents=True)
    (cache / "analyze.json").write_text(json.dumps(report_to_dict(report)) + "\n", encoding="utf-8")
    (cache / "meta.json").write_text(
        json.dumps({"pytest_exit_code": 0, "tests_failed": 0, "usable_coverage": True}), encoding="utf-8"
    )

    def _never(argv: list[str], cwd: Path | None, timeout: int) -> subprocess.CompletedProcess[str]:
        raise AssertionError(f"runner must not be called on cache hit: {argv}")

    result = replay_revision(repo, sha, run=_never)
    assert result.cached is True
    assert result.ok is True
    assert result.usable_coverage is True
    assert result.report is not None
    assert result.report.functions[0].id.as_target() == "src/m.py::f"


# --- graceful skip -------------------------------------------------------


def test_replay_revision_skips_when_clone_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(coverage_replay, "CACHE_DIR", tmp_path / "_cache")
    monkeypatch.setattr(coverage_replay, "CORPUS_DIR", tmp_path / "corpus")
    repo = _repo()

    def _clone_fails(argv: list[str], cwd: Path | None, timeout: int) -> subprocess.CompletedProcess[str]:
        # Any git clone returns non-zero (e.g. offline).
        return subprocess.CompletedProcess(argv, returncode=1, stdout="", stderr="offline")

    result = replay_revision(repo, "b" * 40, run=_clone_fails)
    assert result.ok is False
    assert result.report is None
    assert result.usable_coverage is False


def test_replay_revision_skips_when_coverage_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(coverage_replay, "CACHE_DIR", tmp_path / "_cache")
    monkeypatch.setattr(coverage_replay, "CORPUS_DIR", tmp_path / "corpus")
    repo = _repo()
    sha = "c" * 40

    # Pre-create the clone marker so ensure_clone treats it as present.
    clone = tmp_path / "corpus" / repo.name
    (clone / ".git").mkdir(parents=True)

    def _runner(argv: list[str], cwd: Path | None, timeout: int) -> subprocess.CompletedProcess[str]:
        # git worktree/fetch succeed; the suite "runs" but writes no coverage.json.
        if argv[:2] == ["git", "worktree"]:
            Path(argv[-2]).mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(argv, returncode=0, stdout="1 failed", stderr="")

    result = replay_revision(repo, sha, run=_runner)
    assert result.ok is False
    assert result.usable_coverage is False
    assert result.tests_failed == 1
    # Meta sidecar records the failed run for later discounting.
    meta = json.loads((revision_cache_dir(repo.name, sha) / "meta.json").read_text())
    assert meta["usable_coverage"] is False
