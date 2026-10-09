"""`init --with-baseline` scores from the coverage the first `check` reads (0.3.11).

Until 0.3.11 it always ran one `pytest --cov` at the root and scored from the
`coverage.json` that wrote, whatever `[tool.riskratchet]` named. The baseline then
described coverage `check` never read.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from riskratchet.cli import app

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

runner = CliRunner()

_SOURCE = (
    "def f(x, y):\n"
    "    if x > 1:\n"
    "        if y > 2:\n"
    "            return 1\n"
    "        return 2\n"
    "    for i in range(x):\n"
    "        if i % 2:\n"
    "            y += 1\n"
    "    return y\n"
)
_LINES = list(range(1, 10))


def _report(key: str, *, covered: bool) -> str:
    """A `coverage json` report with one file, fully covered or fully missed."""
    entry = {
        "executed_lines": _LINES if covered else [],
        "missing_lines": [] if covered else _LINES,
    }
    return json.dumps({"files": {key: entry}})


def _stub_pytest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, report: str) -> Path:
    """Put a `pytest` on PATH that writes `report` where `--cov-report=json:` points.

    Returns the marker file the stub touches, so a test can tell whether it ran.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    payload = tmp_path / "stub-report.json"
    payload.write_text(report, encoding="utf-8")
    marker = tmp_path / "pytest-ran"
    stub = bin_dir / "pytest"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'touch "{marker}"\n'
        'for arg in "$@"; do\n'
        '  case "$arg" in --cov-report=json:*) cp '
        f'"{payload}" "${{arg#--cov-report=json:}}";; esac\n'
        "done\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return marker


def _monorepo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two packages, one shard each, keyed relative to the package and fully missed."""
    monkeypatch.chdir(tmp_path)
    for name in ("a", "b"):
        src = tmp_path / "packages" / name / "src"
        src.mkdir(parents=True)
        (src / "mod.py").write_text(_SOURCE, encoding="utf-8")
        (tmp_path / "packages" / name / "coverage.json").write_text(
            _report("src/mod.py", covered=False), encoding="utf-8"
        )
    (tmp_path / "pyproject.toml").write_text(
        "[tool.riskratchet]\n"
        'paths = ["packages"]\n'
        "auto_coverage = false\n"
        "[tool.riskratchet.coverage_map]\n"
        '"packages/a" = "packages/a/coverage.json"\n'
        '"packages/b" = "packages/b/coverage.json"\n',
        encoding="utf-8",
    )


def test_init_baseline_scores_from_the_configured_coverage_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The root report here says "fully covered" and the shards say "fully missed".

    `init` used to score from the root report, so the first `check`, reading the shards,
    saw every function regress.
    """
    _monorepo(tmp_path, monkeypatch)
    marker = _stub_pytest(tmp_path, monkeypatch, _report("packages/a/src/mod.py", covered=True))
    result = runner.invoke(app, ["init", "--with-baseline", "--no-snippet"])
    assert result.exit_code == 0, (result.output, result.stderr)
    assert not marker.exists(), "a coverage_map project gets no root pytest run"
    assert not (tmp_path / "coverage.json").exists()
    check = runner.invoke(app, ["check", "--no-git"])
    assert check.exit_code == 0, (check.output, check.stderr)


def test_init_baseline_names_a_missing_shard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _monorepo(tmp_path, monkeypatch)
    _stub_pytest(tmp_path, monkeypatch, _report("packages/a/src/mod.py", covered=True))
    (tmp_path / "packages" / "b" / "coverage.json").unlink()
    result = runner.invoke(app, ["init", "--with-baseline", "--no-snippet"])
    assert result.exit_code == 2, (result.output, result.stderr)
    assert "coverage-map[packages/b] file not found" in result.stderr
    assert "allow_missing_coverage = true" in result.stderr
    assert not (tmp_path / ".riskratchet.json").exists()


def test_init_baseline_allows_a_missing_shard_when_config_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _monorepo(tmp_path, monkeypatch)
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(
            "auto_coverage = false\n", "auto_coverage = false\nallow_missing_coverage = true\n"
        ),
        encoding="utf-8",
    )
    (tmp_path / "packages" / "b" / "coverage.json").unlink()
    result = runner.invoke(app, ["init", "--with-baseline", "--no-snippet"])
    assert result.exit_code == 0, (result.output, result.stderr)
    assert (tmp_path / ".riskratchet.json").exists()


def test_init_baseline_writes_the_configured_coverage_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`coverage = "build/cov.json"`: the first `check` used to exit 2, report not found."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.py").write_text(_SOURCE, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[tool.riskratchet]\npaths = ["src"]\nauto_coverage = false\ncoverage = "build/cov.json"\n',
        encoding="utf-8",
    )
    _stub_pytest(tmp_path, monkeypatch, _report("src/mod.py", covered=True))
    result = runner.invoke(app, ["init", "--with-baseline", "--no-snippet"])
    assert result.exit_code == 0, (result.output, result.stderr)
    assert (tmp_path / "build" / "cov.json").exists()
    assert not (tmp_path / "coverage.json").exists()
    check = runner.invoke(app, ["check", "--no-git"])
    assert check.exit_code == 0, (check.output, check.stderr)


def test_init_baseline_still_writes_coverage_json_without_a_coverage_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.py").write_text(_SOURCE, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[tool.riskratchet]\npaths = ["src"]\n', encoding="utf-8")
    _stub_pytest(tmp_path, monkeypatch, _report("src/mod.py", covered=True))
    result = runner.invoke(app, ["init", "--with-baseline", "--no-snippet"])
    assert result.exit_code == 0, (result.output, result.stderr)
    assert (tmp_path / "coverage.json").exists()
    assert (tmp_path / ".riskratchet.json").exists()


def test_init_baseline_without_pytest_on_path_is_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordinary state under `uvx riskratchet`. It used to be a FileNotFoundError traceback."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.py").write_text(_SOURCE, encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[tool.riskratchet]\npaths = ["src"]\n', encoding="utf-8")
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    result = runner.invoke(app, ["init", "--with-baseline", "--no-snippet"])
    assert result.exit_code == 2, (result.exit_code, result.exception)
    assert "baseline skipped" in result.stderr
    assert not (tmp_path / ".riskratchet.json").exists()
