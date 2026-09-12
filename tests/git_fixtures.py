"""Real git repositories for tests that depend on git's own behaviour.

Shared rather than duplicated because since 0.3.7 `is_shallow_repo` and the churn root
detection ask git instead of probing the filesystem: a hand-made `.git/shallow` marker no
longer exercises them, so every test that needs a shallow clone needs a genuine one.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def git_env(root: Path) -> dict[str, str]:
    """A git environment with the caller's own stripped out.

    Every `GIT_*` variable is dropped rather than inherited: run under a `git commit` — which
    is exactly what the repo's own pre-commit hook does — the ambient `GIT_INDEX_FILE` and
    `GIT_DIR` point at the *outer* repository, and a fixture repo built with them inherited
    fails with "Error building trees".
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_CONFIG_GLOBAL"] = str(root / ".gitconfig")
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    return env


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, env=git_env(root), capture_output=True)


def init_repo(root: Path) -> None:
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Tester")
    git(root, "config", "commit.gpgsign", "false")


def commit(root: Path, relative: str, body: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    git(root, "add", relative)
    git(root, "commit", "-q", "-m", f"touch {relative}")


def make_shallow_clone(tmp_path: Path) -> Path:
    """Build a genuine depth-1 clone under `tmp_path` and return its working tree."""
    origin = tmp_path / "origin"
    origin.mkdir()
    init_repo(origin)
    commit(origin, "a.py", "x = 1\n")
    commit(origin, "a.py", "x = 2\n")
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", origin.as_uri(), str(clone)],
        check=True,
        capture_output=True,
        env=git_env(tmp_path),
    )
    return clone


def make_nested_config_repo(tmp_path: Path, *, nested: str = "services/api") -> tuple[Path, Path]:
    """A repository whose `[tool.riskratchet]` sits `nested` levels below the git root.

    Returns `(repo_root, config_dir)`. Two levels down by default: one level is the case a
    naive fix happens to get right, so the fixture that pins doctor/check agreement has to be
    deeper than that.
    """
    init_repo(tmp_path)
    config_dir = tmp_path / nested
    (config_dir / "src").mkdir(parents=True)
    (config_dir / "src" / "m.py").write_text(
        "def branchy(x):\n    if x > 0:\n        return 1\n    return 0\n", encoding="utf-8"
    )
    (config_dir / "pyproject.toml").write_text('[tool.riskratchet]\npaths = ["src"]\n', encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-q", "-m", "initial")
    return tmp_path, config_dir
