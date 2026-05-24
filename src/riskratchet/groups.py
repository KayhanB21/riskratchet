"""Workspace/package grouping helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

GroupMap = dict[str, tuple[str, ...]]


def normalize_groups(raw: Any) -> GroupMap:
    """Validate and normalize `[tool.riskratchet.groups]`.

    Config values are name-to-prefix mappings. A value may be a single string
    prefix or a list/tuple of string prefixes for packages that live in more
    than one path.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("[tool.riskratchet.groups] must be a table.")

    groups: GroupMap = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("group names must be non-empty strings.")
        values: Sequence[Any]
        if isinstance(value, str):
            values = (value,)
        elif isinstance(value, list | tuple):
            values = value
        else:
            raise ValueError(f"group {name!r} must be a string prefix or list of string prefixes.")
        prefixes = tuple(_normalize_prefix(prefix, group=name) for prefix in values)
        if not prefixes:
            raise ValueError(f"group {name!r} must define at least one prefix.")
        groups[name] = prefixes
    return groups


def group_for_path(path: str, groups: Mapping[str, Sequence[str]]) -> str | None:
    """Return the group whose prefix is the longest match for `path`."""
    normalized = _normalize_path(path)
    best: tuple[int, str] | None = None
    for name, prefixes in groups.items():
        for prefix in prefixes:
            if _matches_prefix(normalized, prefix):
                candidate = (len(prefix), name)
                if best is None or candidate > best:
                    best = candidate
    return best[1] if best is not None else None


def _normalize_prefix(value: Any, *, group: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"group {group!r} contains an empty or non-string prefix.")
    prefix = _normalize_path(value)
    if prefix.startswith("../") or prefix == "..":
        raise ValueError(f"group {group!r} prefix must be repo-relative.")
    return prefix.rstrip("/")


def _normalize_path(value: str) -> str:
    path = value.replace("\\", "/").strip()
    if path.startswith("./"):
        path = path[2:]
    return PurePosixPath(path).as_posix()


def _matches_prefix(path: str, prefix: str) -> bool:
    if prefix in {"", "."}:
        return True
    return path == prefix or path.startswith(prefix.rstrip("/") + "/")
