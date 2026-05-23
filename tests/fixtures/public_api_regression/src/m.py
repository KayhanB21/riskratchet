"""Public function with low coverage. Fires `public_surface` heavily.

A baseline JSON in this folder records the same function at a lower score
(more coverage). The test asserts that running `check` against this
fixture surfaces a regression for `public_api`.
"""


def public_api(value: int) -> int:
    """Public, public surface fires hard when coverage drops."""
    if value < 0:
        return -1
    if value == 0:
        return 0
    if value < 10:
        return 1
    if value < 100:
        return 2
    return 3


def _private_helper(value: int) -> int:
    return value * 2
