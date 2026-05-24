"""Exercises `__all__`-aware public_surface classification.

`_legacy_exposed` looks private by name but is intentionally promoted via
`__all__`. `naturally_public` is public by both naming and `__all__`.
`_truly_private` is omitted from `__all__` and stays private. The
fixture has zero coverage so any public function will fire
`public_surface` at 100, letting us assert the classification flipped
correctly by reading the component score.
"""

__all__ = ["_legacy_exposed", "naturally_public"]


def _legacy_exposed(value: int) -> int:
    if value < 0:
        return -1
    if value == 0:
        return 0
    return 1


def naturally_public(value: int) -> int:
    return value * 2


def _truly_private(value: int) -> int:
    return value + 1
