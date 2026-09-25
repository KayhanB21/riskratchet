"""The library's one sanctioned write to stderr.

A direct library call that supplies no callback is never silent: the engine's coverage and
parse warnings, the churn collector's errors, and auto-coverage's progress lines fall back to
stderr. Every fallback writes through `write_stderr`, so ruff's `T20` rule flags any new
`print()` in `src/`, and this module holds the only exemption.
"""

from __future__ import annotations

import sys


def write_stderr(message: str) -> None:
    print(message, file=sys.stderr)  # noqa: T201
