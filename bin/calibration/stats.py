"""Pure statistics helpers shared across the calibration harness.

These are the canonical implementations. The frozen P24 script
(``bin/experiments/sprawl_vs_complexity.py``) carries byte-identical copies it
loads standalone; ``tests/test_calibration_stats.py`` pins the two in parity so
they cannot drift. No I/O, no riskratchet imports — just numbers in, numbers out.
"""

from __future__ import annotations

import math


def pearson(xs: list[float], ys: list[float]) -> float:
    """Pearson correlation; NaN when undefined (n < 2 or a zero-variance input)."""
    n = len(xs)
    if n < 2:
        return float("nan")
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x == 0 or var_y == 0:
        return float("nan")
    return cov / math.sqrt(var_x * var_y)


def rank(values: list[float]) -> list[float]:
    """Average ranks (1-based), so ties share the mean of their positions."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float:
    """Spearman rank correlation; NaN when undefined."""
    if len(xs) < 2:
        return float("nan")
    return pearson(rank(xs), rank(ys))


def mann_whitney_u(group_a: list[float], group_b: list[float]) -> dict[str, float]:
    """Mann-Whitney U for two independent samples, with a normal-approx z/effect.

    Used by the candidate re-scoring (``rescore.py``) to ask whether a candidate
    makes *rejected* PRs carry more regressions than *accepted* ones. Returns the
    U statistic for ``group_a``, the rank-biserial effect size (``+1`` => every
    a > every b), and a tie-corrected normal-approximation z. NaN-filled when
    either group is empty.
    """
    n_a, n_b = len(group_a), len(group_b)
    if n_a == 0 or n_b == 0:
        return {"u": float("nan"), "effect": float("nan"), "z": float("nan")}
    pooled = group_a + group_b
    ranks = rank(pooled)
    rank_sum_a = sum(ranks[:n_a])
    u_a = rank_sum_a - n_a * (n_a + 1) / 2.0
    # Rank-biserial correlation: U_a normalized to [-1, 1].
    effect = 2.0 * u_a / (n_a * n_b) - 1.0
    # Tie-corrected normal approximation.
    n = n_a + n_b
    mean_u = n_a * n_b / 2.0
    counts: dict[float, int] = {}
    for v in pooled:
        counts[v] = counts.get(v, 0) + 1
    tie_term = sum(t**3 - t for t in counts.values())
    var_u = (n_a * n_b / 12.0) * ((n + 1) - tie_term / (n * (n - 1))) if n > 1 else 0.0
    z = (u_a - mean_u) / math.sqrt(var_u) if var_u > 0 else float("nan")
    return {"u": u_a, "effect": effect, "z": z}


def distribution(values: list[float]) -> dict[str, object]:
    """Quantiles + a 0..100-by-10 histogram + zero fraction for a score series."""
    if not values:
        return {"n": 0}
    s = sorted(values)
    n = len(s)

    def q(p: float) -> float:
        idx = min(n - 1, max(0, round(p * (n - 1))))
        return round(s[idx], 2)

    hist = [0] * 10
    for v in values:
        hist[min(9, max(0, int(v // 10)))] += 1
    return {
        "n": n,
        "min": round(s[0], 2),
        "p25": q(0.25),
        "median": q(0.5),
        "p75": q(0.75),
        "max": round(s[-1], 2),
        "zeros_frac": round(sum(1 for v in values if v == 0.0) / n, 3),
        "hist_0_100_by_10": hist,
    }
