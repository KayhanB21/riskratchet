"""P24 investigation: does the per-file `sprawl` component over-reward file splitting?

This grounds the 0.2.9 roadmap question in numbers. The conclusion lives in
``docs/sprawl-component-finding.md``; this script produces the evidence.

What it does:

1. **Orthogonality.** Pearson *and* Spearman correlation between the
   per-function ``structural_complexity`` component and ``sprawl`` (and between
   ``structural_complexity`` and each half of sprawl). Spearman is the
   load-bearing statistic: the component scores are clamped to [0,100],
   saturate, and are heavily zero-inflated, so Pearson is fragile here. We
   report both and the distributions so the reader can judge.

2. **File-level leakage.** Sprawl blends a per-function term (function length)
   with a per-file term (total file lines). The file-line term is identical for
   every function in a file — a file-level property injected into a per-function
   score.

3. **Split simulation.** Recompute scores as if a file were split in half (same
   functions, half the lines). Any change is a pure artifact of the file-line
   term.

Corpus: riskratchet's own ``src`` plus, when ``--clone`` is given and the
network is reachable, a few external OSS repos (requests, httpx, rich) cloned
shallow into ``data/calibration/corpus/`` (gitignored). A single self-corpus is
*not* generalizable; the multi-repo pooled numbers are the headline, and even
those measure inter-metric correlation, **not** predictive validity (whether
sprawl predicts defects / review time). Only labelled outcomes (P21) settle
that.

Run:
  uv run python bin/experiments/sprawl_vs_complexity.py            # self only
  uv run python bin/experiments/sprawl_vs_complexity.py --clone    # + OSS corpus
  uv run python bin/experiments/sprawl_vs_complexity.py PATH ...   # explicit paths

Output: a human summary on stdout and a JSON record at
``data/calibration/sprawl-experiment.json``. Changes no weights.
"""

from __future__ import annotations

import json
import math
import subprocess
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
CORPUS_DIR = REPO_ROOT / "data" / "calibration" / "corpus"

# Shallow-clone targets for the external corpus (well-maintained, diverse).
CORPUS = {
    "requests": "https://github.com/psf/requests",
    "httpx": "https://github.com/encode/httpx",
    "rich": "https://github.com/Textualize/rich",
}


# --- statistics ----------------------------------------------------------


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


def _rank(values: list[float]) -> list[float]:
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


def _spearman(xs: list[float], ys: list[float]) -> float:
    if len(xs) < 2:
        return float("nan")
    return _pearson(_rank(xs), _rank(ys))


def _distribution(values: list[float]) -> dict[str, object]:
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


# --- sprawl decomposition + split simulation -----------------------------


def _file_line_term(total_lines: int) -> float:
    return _saturate(total_lines, free=FILE_LINE_FREE, saturation=FILE_LINE_SATURATION)


def _function_line_term(line_count: int) -> float:
    return _saturate(line_count, free=FUNCTION_LINE_FREE, saturation=FUNCTION_LINE_SATURATION)


def _score_with_file_lines(fn: FunctionRisk, total_lines: int) -> float:
    new_stats: FileStats = replace(fn.file_stats, total_lines=total_lines)
    new_components = replace(fn.components, sprawl=sprawl_score(fn.span, new_stats))
    return total_risk(new_components, weights=DEFAULT_WEIGHTS)


def _split_simulation(fns: list[FunctionRisk]) -> dict[str, object]:
    """Halve the lines of a file that straddles the 500-1000 band (max effect)."""
    straddlers = [f for f in fns if FILE_LINE_FREE < f.file_stats.total_lines < FILE_LINE_SATURATION]
    pool = straddlers or fns
    target = max(pool, key=lambda f: f.file_stats.total_lines)
    path = target.file_stats.path
    original = target.file_stats.total_lines
    half = max(1, original // 2)
    rows = []
    for fn in fns:
        if fn.file_stats.path != path:
            continue
        before = total_risk(fn.components, weights=DEFAULT_WEIGHTS)
        after = _score_with_file_lines(fn, half)
        rows.append({"qualname": fn.id.qualname, "delta": round(after - before, 2)})
    rows.sort(key=lambda r: r["delta"])
    return {
        "file": path,
        "original_total_lines": original,
        "simulated_total_lines": half,
        "straddles_band": bool(straddlers),
        "max_score_drop": min((r["delta"] for r in rows), default=0.0),
    }


def _metrics(fns: list[FunctionRisk]) -> dict[str, list[float]]:
    return {
        "sprawl": [fn.components.sprawl for fn in fns],
        "structural": [fn.components.structural_complexity for fn in fns],
        "file_line": [_file_line_term(fn.file_stats.total_lines) for fn in fns],
        "func_line": [_function_line_term(fn.span.line_count) for fn in fns],
    }


def _correlations(m: dict[str, list[float]]) -> dict[str, object]:
    def pair(a: str, b: str) -> dict[str, float]:
        return {
            "pearson": round(_pearson(m[a], m[b]), 4),
            "spearman": round(_spearman(m[a], m[b]), 4),
        }

    return {
        "sprawl_vs_structural": pair("sprawl", "structural"),
        "file_line_vs_structural": pair("file_line", "structural"),
        "func_line_vs_structural": pair("func_line", "structural"),
        "file_line_vs_func_line": pair("file_line", "func_line"),
    }


def analyze_paths(label: str, paths: list[Path], root: Path) -> dict[str, object] | None:
    report = analyze(paths, root=root, use_git=False)
    fns = list(report.functions)
    if not fns:
        return None
    m = _metrics(fns)
    n_files = len({fn.file_stats.path for fn in fns})
    return {
        "label": label,
        "n_functions": len(fns),
        "n_files": n_files,
        "correlations": _correlations(m),
        "distributions": {key: _distribution(values) for key, values in m.items()},
        "split_simulation": _split_simulation(fns),
        "_metrics": m,  # popped before serialization; used for pooling
    }


# --- corpus cloning ------------------------------------------------------


def _ensure_clone(name: str, url: str) -> Path | None:
    dest = CORPUS_DIR / name
    if dest.exists():
        return dest
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dest)],
            check=True,
            capture_output=True,
            timeout=180,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None
    return dest


# --- synthetic demonstration ---------------------------------------------


def synthetic_grid() -> dict[str, object]:
    """Identical 40-line, CC=8 function in files of varying size.

    Shows the score moving on file size alone while structural_complexity holds.
    """
    from riskratchet.models import ChurnStats, ComplexityStats, CoverageStats, FunctionSpan
    from riskratchet.scoring import compute_components

    span = FunctionSpan(start_line=1, end_line=40)
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
    args = argv[1:]
    do_clone = "--clone" in args
    explicit = [Path(a) for a in args if not a.startswith("--")]

    corpora: list[dict[str, object]] = []
    self_result = analyze_paths("self (src/riskratchet)", [REPO_ROOT / "src"], REPO_ROOT)
    if self_result is not None:
        corpora.append(self_result)
    for path in explicit:
        res = analyze_paths(str(path), [path], path)
        if res is not None:
            corpora.append(res)
    if do_clone:
        for name, url in CORPUS.items():
            repo = _ensure_clone(name, url)
            if repo is None:
                print(f"  (skipped {name}: clone failed / offline)")
                continue
            res = analyze_paths(name, [repo], repo)
            if res is not None:
                corpora.append(res)

    # Pooled correlations across every analyzed corpus.
    metric_keys = ("sprawl", "structural", "file_line", "func_line")
    pooled = {key: [v for c in corpora for v in c["_metrics"][key]] for key in metric_keys}
    pooled_block = {
        "n_functions": len(pooled["sprawl"]),
        "n_corpora": len(corpora),
        "correlations": _correlations(pooled),
        "distributions": {key: _distribution(values) for key, values in pooled.items()},
    }

    for c in corpora:
        c.pop("_metrics", None)

    result = {
        "synthetic": synthetic_grid(),
        "corpora": corpora,
        "pooled": pooled_block,
        "notes": [
            "Spearman is primary: components are clamped/saturated/zero-inflated, "
            "so Pearson understates monotonic association.",
            "Correlation here is inter-metric, NOT predictive validity. Whether "
            "sprawl predicts defects/review-time needs labelled outcomes (P21).",
            "A single self-corpus is not generalizable; use --clone for the pooled multi-repo numbers.",
        ],
    }

    out_path = REPO_ROOT / "data" / "calibration" / "sprawl-experiment.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    print("=== Synthetic: identical function, varying file size ===")
    for row in result["synthetic"]["rows"]:  # type: ignore[index]
        print(
            f"  file_lines={row['file_total_lines']:>5}  sprawl={row['sprawl']:>6}  "
            f"structural={row['structural_complexity']:>6}  score={row['total_score']:>6}"
        )
    print()
    for c in corpora:
        corr = c["correlations"]["sprawl_vs_structural"]  # type: ignore[index]
        print(
            f"=== {c['label']}: {c['n_functions']} fns / {c['n_files']} files ===\n"
            f"  corr(sprawl, structural): pearson={corr['pearson']} spearman={corr['spearman']}"
        )
    pc = pooled_block["correlations"]["sprawl_vs_structural"]  # type: ignore[index]
    pf = pooled_block["correlations"]["file_line_vs_structural"]  # type: ignore[index]
    print()
    print(f"=== POOLED ({pooled_block['n_functions']} fns across {pooled_block['n_corpora']} corpora) ===")
    print(f"  corr(sprawl, structural):    pearson={pc['pearson']} spearman={pc['spearman']}")
    print(f"  corr(file_line, structural): pearson={pf['pearson']} spearman={pf['spearman']}")
    print()
    print(f"Wrote {out_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
