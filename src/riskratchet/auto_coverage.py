"""Auto-generate coverage.json when the user hasn't produced one.

This removes the "you must wire pytest --cov before riskratchet" footgun from
pre-commit and ad-hoc local runs. The flow is:

  1. If the user explicitly pointed `--coverage` at an existing file, use it.
  2. Otherwise, look at `.riskratchet/coverage.json` (configurable). If it's
     newer than every `.py` file under the scan paths, reuse it.
  3. Otherwise, shell out to the configured test command (default
     `pytest --cov --cov-branch --cov-report=json:{output} -q`), which must
     write a coverage JSON file at `{output}`. The cache is then reused.

Disabled with `--no-auto-cov` or `[tool.riskratchet] auto_coverage = false`.
"""

from __future__ import annotations

import shlex
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

Runner = Callable[[str, Path], int]
Logger = Callable[[str], None]

DEFAULT_CACHE_PATH = Path(".riskratchet/coverage.json")
DEFAULT_TEST_COMMAND = "pytest --cov --cov-branch --cov-report=json:{output} -q"


class CoverageCommandError(Exception):
    """The auto-coverage test command could not be started at all.

    Distinct from the command *running* and failing, which is survivable —
    whatever coverage it wrote is still used. This is "there is nothing to
    run", so it is a setup error (exit 2), never a gate verdict (exit 1).
    """

    def __init__(self, command: str, problem: str) -> None:
        super().__init__(f"test command {problem}")
        self.command = command
        self.problem = problem


@dataclass(frozen=True, slots=True)
class AutoCoverageResult:
    """Outcome of `ensure_coverage`.

    `path` is the coverage JSON to feed downstream, or None if no coverage is
    available. `source` records where it came from for logging and tests.
    `command` is the test command actually run (empty when no run happened).
    `returncode` is the test command's exit status (0 when no run happened).
    """

    path: Path | None
    source: str
    command: str = ""
    returncode: int = 0


def ensure_coverage(
    *,
    requested: Path | None,
    sources: Iterable[Path],
    cache_path: Path,
    test_command: str,
    enabled: bool,
    cwd: Path = Path("."),
    runner: Runner | None = None,
    log: Logger | None = None,
) -> AutoCoverageResult:
    """Return a usable coverage JSON path, generating it via tests if needed.

    `cwd` is the working directory for the test command, so auto-coverage
    measures the whole project (the config directory) rather than whatever
    nested directory the CLI happened to be invoked from.
    """
    say = log or _stderr_log

    if requested is not None and requested.exists():
        return AutoCoverageResult(path=requested, source="explicit")

    if not enabled:
        return AutoCoverageResult(path=None, source="disabled")

    sources_list = [Path(s) for s in sources]
    if cache_path.exists() and _cache_is_fresh(cache_path, sources_list):
        return AutoCoverageResult(path=cache_path, source="cache")

    command = test_command.format(output=str(cache_path))
    say(f"riskratchet: no fresh coverage data; running `{command}`")
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    invoke = runner or _default_runner
    returncode = invoke(command, cwd)
    if returncode != 0:
        say(f"riskratchet: test command exited {returncode}. Continuing with whatever coverage was written.")
    if not cache_path.exists():
        say(
            f"riskratchet: test command did not produce {cache_path}.\n"
            "\n"
            "Fix one of:\n"
            f"  1. Update the test command to write coverage at that path:\n"
            f"       pytest --cov --cov-branch --cov-report=json:{cache_path} -q\n"
            "  2. Point --coverage at an existing coverage.json:\n"
            "       <command> --coverage <path>\n"
            "  3. Disable auto-coverage entirely:\n"
            "       <command> --no-auto-cov"
        )
        return AutoCoverageResult(
            path=None,
            source="generated_missing",
            command=command,
            returncode=returncode,
        )
    return AutoCoverageResult(
        path=cache_path,
        source="generated",
        command=command,
        returncode=returncode,
    )


def _cache_is_fresh(cache_path: Path, sources: list[Path]) -> bool:
    cache_mtime = cache_path.stat().st_mtime
    for source in sources:
        if not source.exists():
            continue
        if source.is_file():
            if source.suffix == ".py" and source.stat().st_mtime > cache_mtime:
                return False
            continue
        for py_file in source.rglob("*.py"):
            if py_file.stat().st_mtime > cache_mtime:
                return False
    return True


def _split_command(command: str) -> list[str]:
    """Parse a configured test command into argv, or say why it cannot be."""
    try:
        args = shlex.split(command)
    except ValueError as exc:  # unbalanced quote in a configured test_command
        raise CoverageCommandError(command, f"could not be parsed: {exc}") from exc
    if not args:
        raise CoverageCommandError(command, "is empty")
    return args


def _default_runner(command: str, cwd: Path) -> int:
    # shell=False keeps argument quoting honest. The configured command is
    # split with shlex so users can still write a single template string.
    try:
        result = subprocess.run(_split_command(command), check=False, cwd=cwd)
    except OSError as exc:
        # `pytest` not on PATH is the common one, and it used to surface as an
        # uncaught FileNotFoundError: a Rich traceback and exit 1, which means
        # "the gate tripped". A test runner that cannot be started is a setup
        # error the user must fix, so it has to reach exit 2 instead.
        raise CoverageCommandError(command, f"could not be run: {exc.strerror or exc}") from exc
    return result.returncode


def _stderr_log(message: str) -> None:
    print(message, file=sys.stderr)
