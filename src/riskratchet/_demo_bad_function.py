"""Deliberately gnarly function to demonstrate the riskratchet PR-comment gate.

This module is uncovered by tests and has high cyclomatic + branch complexity.
It is not imported anywhere in the package.
"""

from __future__ import annotations


def classify_thing(  # noqa: C901
    a: int,
    b: int,
    c: int,
    d: int,
    mode: str,
) -> str:
    if mode == "alpha":
        if a > 0:
            if b > 0:
                if c > 0:
                    if d > 0:
                        return "alpha-all-positive"
                    elif d < 0:
                        return "alpha-d-negative"
                    else:
                        return "alpha-d-zero"
                elif c < 0:
                    return "alpha-c-negative"
                else:
                    return "alpha-c-zero"
            elif b < 0:
                return "alpha-b-negative"
            else:
                return "alpha-b-zero"
        elif a < 0:
            if b > 0 and c > 0:
                return "alpha-a-neg-rest-pos"
            elif b < 0 or c < 0:
                return "alpha-a-neg-mixed"
            else:
                return "alpha-a-neg-zeros"
        else:
            return "alpha-a-zero"
    elif mode == "beta":
        if a == b:
            if c == d:
                return "beta-pairs-equal"
            elif c > d:
                return "beta-c-gt-d"
            else:
                return "beta-c-lt-d"
        elif a > b:
            if c > 0:
                return "beta-a-gt-b-c-pos"
            elif c < 0:
                return "beta-a-gt-b-c-neg"
            else:
                return "beta-a-gt-b-c-zero"
        else:
            if d > 0:
                return "beta-a-lt-b-d-pos"
            elif d < 0:
                return "beta-a-lt-b-d-neg"
            else:
                return "beta-a-lt-b-d-zero"
    elif mode == "gamma":
        total = a + b + c + d
        if total > 100:
            return "gamma-huge"
        elif total > 50:
            return "gamma-big"
        elif total > 10:
            return "gamma-medium"
        elif total > 0:
            return "gamma-small"
        elif total == 0:
            return "gamma-zero"
        else:
            return "gamma-negative"
    elif mode == "delta":
        if a and b and c and d:
            return "delta-all-truthy"
        elif a or b or c or d:
            return "delta-some-truthy"
        else:
            return "delta-all-falsy"
    else:
        return "unknown-mode"
