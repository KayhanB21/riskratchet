"""End-to-end SZZ on a real (local, networkless) git repo.

Plants commit A that introduces a function with a buggy line, then commit B that
fixes that line, and asserts the SZZ chain (deleted lines -> blame -> enclosing
function at the parent) attributes the defect to commit A and to the right
function. git is available in CI; no network, no gh, no clone.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from bin.calibration.szz import implications_for_fix

from riskratchet.models import FunctionId

_A = """\
def helper():
    return 1


def parse(text):
    value = text.strip()
    if value == "BUG":
        return None
    return value
"""

# Same file, the line inside `parse` modified (a deletion+addition of one line).
_B = """\
def helper():
    return 1


def parse(text):
    value = text.strip()
    if not value:
        return None
    return value
"""


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


def test_szz_attributes_fix_to_introducing_function(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    sha_a = _commit(tmp_path, "m.py", _A, "feat: add parse")
    sha_b = _commit(tmp_path, "m.py", _B, "fix: handle empty input")

    impls = implications_for_fix(tmp_path, sha_b, ("m.py",))

    assert impls, "expected at least one implication"
    # The buggy line lived inside `parse` and was introduced by commit A.
    assert all(i.parent_fn_id == FunctionId("m.py", "parse") for i in impls)
    assert all(i.introducer_sha == sha_a for i in impls)
    assert all(i.fix_sha == sha_b for i in impls)


def test_szz_pure_addition_fix_yields_no_implication(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _commit(tmp_path, "m.py", _A, "feat: add parse")
    # A fix that only INSERTS a line (no deletion) has nothing to blame.
    added = _A.replace(
        "    value = text.strip()\n", "    value = text.strip()\n    assert text is not None\n"
    )
    sha_fix = _commit(tmp_path, "m.py", added, "fix: guard None")

    impls = implications_for_fix(tmp_path, sha_fix, ("m.py",))
    assert impls == []
