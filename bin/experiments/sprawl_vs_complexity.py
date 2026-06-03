"""P24 investigation: does the per-file `sprawl` component over-reward file splitting?

This script grounds the 0.2.9 roadmap question in numbers. It does three things
against a real corpus (riskratchet's own ``src/`` by default — no network or
clone needed) plus a controlled synthetic grid:

1. **Orthogonality.** Pearson correlation between the per-function
   ``structural_complexity`` component and ``sprawl``, and between
   ``structural_complexity`` and the *file-line half* of sprawl in isolation.
   If sprawl simply re-measured complexity, we'd expect a high correlation.

2. **File-level leakage.** Sprawl blends a per-function term (function length)
   with a per-file term (total file lines). The file-line term is identical for
   every function in a file, so it injects a file-level property into a
   per-function score. We report, per file, how much of each function's sprawl
   is the shared file-line term.

3. **Split simulation.** For the largest file, we recompute every function's
   score as if the file were split in half (same functions, half the lines).
   Function bodies are byte-identical, so any score change is a pure metric
   artifact of the file-line term. We report the per-function deltas.

Run: ``uv run python bin/experiments/sprawl_vs_complexity.py``
Optionally pass one or more paths to analyze instead of ``src``.

Output: a human summary on stdout and a JSON record at
``data/calibration/sprawl-experiment.json`` (relative to the repo root).

This is research tooling, not a gate. It changes no weights; the written
conclusion lives in ``docs/sprawl-component-finding.md``.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import replace
from pathlib import Path

from riskratchet.engine import analyze
from riskratchet.models import FileStats, FunctionRisk
from riskratchet.scoring import (
    DEFAULT_WEIGHTS,
    FILE_LINE_FREE,
    FILE_LINE_SATURATION,
    FUNCTION_LINE_FREE,
    FUNCTION_LINE_SATURATION,
    _saturate,
    sprawl_score,
    total_risk,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _pearson(xs: list[float], ys: list[float]) -> float:
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


def _file_line_term(total_lines: int) -> float:
    return _saturate(total_lines, free=FILE_LINE_FREE, saturation=FILE_LINE_SATURATION)


def _function_line_term(line_count: int) -> float:
    return _saturate(line_count, free=FUNCTION_LINE_FREE, saturation=FUNCTION_LINE_SATURATION)


def _score_with_file_lines(fn: FunctionRisk, total_lines: int) -> float:
    """Recompute a function's total score as if its file had `total_lines` lines."""
    new_stats: FileStats = replace(fn.file_stats, total_lines=total_lines)
    new_components = replace(fn.components, sprawl=sprawl_score(fn.span, new_stats))
    return total_risk(new_components, weights=DEFAULT_WEIGHTS)


def analyze_corpus(paths: list[Path]) -> dict[str, object]:
    report = analyze(paths, root=REPO_ROOT, use_git=False)
    fns = list(report.functions)
    if not fns:
        raise SystemExit(f"no functions found under {paths}")

    sprawl = [fn.components.sprawl for fn in fns]
    structural = [fn.components.structural_complexity for fn in fns]
    file_line = [_file_line_term(fn.file_stats.total_lines) for fn in fns]
    func_line = [_function_line_term(fn.span.line_count) for fn in fns]

    # Per-file: how much of sprawl is the shared file-line term.
    by_file: dict[str, dict[str, float]] = {}
    for fn in fns:
        path = fn.file_stats.path
        if path not in by_file:
            flt = _file_line_term(fn.file_stats.total_lines)
            by_file[path] = {
                "total_lines": float(fn.file_stats.total_lines),
                "file_line_term": round(flt, 2),
                "functions": 0,
            }
        by_file[path]["functions"] += 1

    # Split simulation on the largest file.
    largest = max(fns, key=lambda f: f.file_stats.total_lines)
    largest_path = largest.file_stats.path
    original_lines = largest.file_stats.total_lines
    half_lines = max(1, original_lines // 2)
    split_rows = []
    for fn in fns:
        if fn.file_stats.path != largest_path:
            continue
        before = total_risk(fn.components, weights=DEFAULT_WEIGHTS)
        after = _score_with_file_lines(fn, half_lines)
        split_rows.append(
            {
                "qualname": fn.id.qualname,
                "score_before": round(before, 2),
                "score_after_split": round(after, 2),
                "delta": round(after - before, 2),
            }
        )
    split_rows.sort(key=lambda r: r["delta"])
    max_drop = min((r["delta"] for r in split_rows), default=0.0)

    return {
        "corpus": [str(p) for p in paths],
        "n_functions": len(fns),
        "n_files": len(by_file),
        "correlation": {
            "sprawl_vs_structural": round(_pearson(sprawl, structural), 4),
            "file_line_term_vs_structural": round(_pearson(file_line, structural), 4),
            "function_line_term_vs_structural": round(_pearson(func_line, structural), 4),
            "file_line_term_vs_function_line_term": round(_pearson(file_line, func_line), 4),
        },
        "split_simulation": {
            "file": largest_path,
            "original_total_lines": original_lines,
            "simulated_total_lines": half_lines,
            "weight_sprawl": DEFAULT_WEIGHTS["sprawl"],
            "max_score_drop": max_drop,
            "functions": split_rows,
        },
        "per_file_file_line_term": by_file,
    }


def synthetic_grid() -> dict[str, object]:
    """Two functions, identical (length, complexity), differing only in file size.

    Demonstrates that splitting a file (halving total_lines) lowers the score of
    byte-identical functions purely through the sprawl file-line term, while
    structural_complexity is untouched.
    """
    from riskratchet.models import (
        ChurnStats,
        ComplexityStats,
        CoverageStats,
        FunctionSpan,
    )
    from riskratchet.scoring import compute_components

    span = FunctionSpan(start_line=1, end_line=40)  # 40-line function (below the 80-line free band)
    complexity = ComplexityStats(cyclomatic=8)
    coverage = CoverageStats(line_coverage=0.5, branch_coverage=0.5)
    churn = ChurnStats(commits=0)

    rows = []
    for total_lines in (300, 600, 1200):
        stats = FileStats(path="m.py", total_lines=total_lines, function_count=10)
        comp = compute_components(
            is_public=True,
            span=span,
            complexity=complexity,
            coverage=coverage,
            churn=churn,
            file_stats=stats,
        )
        rows.append(
            {
                "file_total_lines": total_lines,
                "sprawl": round(comp.sprawl, 2),
                "structural_complexity": round(comp.structural_complexity, 2),
                "total_score": round(total_risk(comp, weights=DEFAULT_WEIGHTS), 2),
            }
        )
    return {"description": "identical 40-line, CC=8 function in files of varying size", "rows": rows}


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv[1:]] or [REPO_ROOT / "src"]
    result = {
        "synthetic": synthetic_grid(),
        "corpus_analysis": analyze_corpus(paths),
    }

    out_path = REPO_ROOT / "data" / "calibration" / "sprawl-experiment.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    corpus = result["corpus_analysis"]
    syn = result["synthetic"]
    print("=== Synthetic: identical function, varying file size ===")
    for row in syn["rows"]:  # type: ignore[index]
        print(
            f"  file_lines={row['file_total_lines']:>5}  "
            f"sprawl={row['sprawl']:>6}  structural={row['structural_complexity']:>6}  "
            f"score={row['total_score']:>6}"
        )
    print()
    print(f"=== Corpus: {corpus['corpus']} ({corpus['n_functions']} fns, {corpus['n_files']} files) ===")
    corr = corpus["correlation"]  # type: ignore[index]
    print(f"  corr(sprawl, structural_complexity)          = {corr['sprawl_vs_structural']}")
    print(f"  corr(file_line_term, structural_complexity)  = {corr['file_line_term_vs_structural']}")
    print(f"  corr(func_line_term, structural_complexity)  = {corr['function_line_term_vs_structural']}")
    sim = corpus["split_simulation"]  # type: ignore[index]
    print()
    print(
        f"=== Split simulation on {sim['file']} "
        f"({sim['original_total_lines']} -> {sim['simulated_total_lines']} lines) ==="
    )
    print(f"  max score drop from a cosmetic split: {sim['max_score_drop']}")
    print("  (every function in the file moves; bodies unchanged)")
    print()
    print(f"Wrote {out_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
