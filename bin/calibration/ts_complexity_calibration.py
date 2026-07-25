"""Derive ``TYPESCRIPT_COMPLEXITY_CALIBRATION`` by endpoint-percentile matching (B2, 0.3.0).

The two backends count cyclomatic complexity by intentionally different rules (TS is
ESLint-faithful, Python is radon/`ast`-faithful — see `docs/language-backend-contract.md §3`), so
the *raw* counts are not comparable and the gap is not a constant offset. The fix committed for
0.3.0 is per-language *normalization*: keep each raw count as displayed, but give TS its own
`(free, saturation)` band so the normalized 0-100 `structural_complexity` component represents the
same **distribution position** in either language.

This tool derives that band, never hand-picks it:

1. Measure the per-function cyclomatic distribution of a Python corpus and a TypeScript corpus.
2. Python normalizes with ``PYTHON_COMPLEXITY_CALIBRATION = (free=1, saturation=21)``. Find the
   percentiles those two endpoints occupy in the Python distribution:
   ``p_free = P(py_cc <= 1)``, ``p_sat = P(py_cc <= 21)``.
3. Read the TS cyclomatic values at those **same percentiles**:
   ``free_ts = quantile(ts_cc, p_free)``, ``sat_ts = quantile(ts_cc, p_sat)``.

So a TS function at percentile ``p`` gets (at the two anchors, and approximately in between) the same
normalized score a Python function at percentile ``p`` gets — language-fair at the gate, while the
displayed ``cx N`` stays ESLint-faithful.

Analysis only. Emits `data/calibration/ts-complexity-calibration.json`; wires nothing into scoring
(the constant lands in `scoring.py` in the same B2 change, still unconsumed until B3). Run:

    uv run --all-extras python -m bin.calibration.ts_complexity_calibration
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib  # type: ignore[import-not-found]

from bin.calibration.config import load_corpus
from bin.calibration.corpus import CACHE_DIR, CALIBRATION_DIR, CORPUS_DIR, analyze_functions
from riskratchet.scoring import PYTHON_COMPLEXITY_CALIBRATION, _saturate
from riskratchet.typescript import analyze_ts_file

MANIFEST = CALIBRATION_DIR / "ts-complexity-corpus.toml"
OUTPUT = CALIBRATION_DIR / "ts-complexity-calibration.json"
TS_CACHE = CACHE_DIR / "ts-complexity"

# Files whose bodies are not representative product code: tests/specs/stories inflate the low end
# with thin `expect(...)` chains; `.d.ts` declarations carry no function bodies at all.
_EXCLUDE_SUFFIXES = (".d.ts",)
_EXCLUDE_PARTS = frozenset({"__tests__", "__mocks__", "test", "tests", "spec", "specs", "fixtures"})
_EXCLUDE_INFIXES = (".test.", ".spec.", ".stories.", ".bench.")


@dataclass(frozen=True)
class TsRepo:
    name: str
    url: str
    ref: str
    paths: tuple[str, ...]


def _load_manifest(path: Path) -> list[TsRepo]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    repos = []
    for entry in data.get("repo", []):
        repos.append(
            TsRepo(
                name=str(entry["name"]),
                url=str(entry["url"]),
                ref=str(entry.get("ref", "main")),
                paths=tuple(str(p) for p in entry.get("paths", [])),
            )
        )
    return repos


def _is_product_file(path: Path) -> bool:
    name = path.name
    if name.endswith(_EXCLUDE_SUFFIXES):
        return False
    if any(infix in name for infix in _EXCLUDE_INFIXES):
        return False
    return not any(part in _EXCLUDE_PARTS for part in path.parts)


def _clone(repo: TsRepo, *, cache: Path) -> str:
    """Shallow-clone `repo` into the cache (idempotent) and return the resolved commit SHA."""
    dest = cache / repo.name
    if not dest.exists():
        cache.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", repo.ref, repo.url, str(dest)],
            check=True,
            capture_output=True,
            text=True,
        )
    sha = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    )
    return sha.stdout.strip()


def _ts_ccs(repos: list[TsRepo], *, cache: Path) -> tuple[list[int], list[dict[str, object]]]:
    ccs: list[int] = []
    provenance: list[dict[str, object]] = []
    for repo in repos:
        sha = _clone(repo, cache=cache)
        root = cache / repo.name
        roots = [root / p for p in repo.paths] if repo.paths else [root]
        files = [
            f
            for base in roots
            if base.exists()
            for f in base.rglob("*")
            if f.suffix in {".ts", ".tsx", ".mts", ".cts"} and _is_product_file(f)
        ]
        repo_ccs: list[int] = []
        for f in files:
            fns, _ = analyze_ts_file(f, root=root)
            repo_ccs.extend(fn.complexity.cyclomatic for fn in fns if fn.complexity is not None)
        ccs.extend(repo_ccs)
        provenance.append(
            {"name": repo.name, "ref": repo.ref, "sha": sha, "files": len(files), "functions": len(repo_ccs)}
        )
    return ccs, provenance


def _python_ccs() -> tuple[list[int], list[dict[str, object]]]:
    ccs: list[int] = []
    provenance: list[dict[str, object]] = []
    for repo in load_corpus():
        root = CORPUS_DIR / repo.name
        if not root.exists():
            continue
        paths = [root / p for p in repo.paths if (root / p).exists()] or [root]
        fns = analyze_functions(paths, root)
        repo_ccs = [fn.complexity.cyclomatic for fn in fns]
        ccs.extend(repo_ccs)
        provenance.append(
            {
                "name": repo.name,
                "tier": "messy" if repo.coverage_free else "polished",
                "functions": len(repo_ccs),
            }
        )
    return ccs, provenance


def _quantile(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolation quantile (numpy's default 'linear' method), pure-Python so the
    derivation needs no numpy and is exactly reproducible."""
    if not sorted_vals:
        raise ValueError("empty distribution")
    if q <= 0:
        return sorted_vals[0]
    if q >= 1:
        return sorted_vals[-1]
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    frac = pos - lo
    if lo + 1 >= len(sorted_vals):
        return sorted_vals[lo]
    return sorted_vals[lo] * (1 - frac) + sorted_vals[lo + 1] * frac


def _fraction_leq(values: list[int], threshold: float) -> float:
    return sum(1 for v in values if v <= threshold) / len(values)


def _distribution(values: list[int]) -> dict[str, object]:
    s = sorted(values)
    return {
        "n": len(s),
        "min": s[0],
        "max": s[-1],
        "mean": round(sum(s) / len(s), 3),
        "p50": _quantile([float(v) for v in s], 0.50),
        "p90": _quantile([float(v) for v in s], 0.90),
        "p95": _quantile([float(v) for v in s], 0.95),
        "p99": _quantile([float(v) for v in s], 0.99),
    }


def derive(python_ccs: list[int], ts_ccs: list[int]) -> dict[str, object]:
    py_free, py_sat = PYTHON_COMPLEXITY_CALIBRATION
    p_free = _fraction_leq(python_ccs, py_free)
    p_sat = _fraction_leq(python_ccs, py_sat)
    ts_sorted = [float(v) for v in sorted(ts_ccs)]
    free_ts_raw = _quantile(ts_sorted, p_free)
    sat_ts_raw = _quantile(ts_sorted, p_sat)
    # Round to whole complexity points for an interpretable, stable band (the metric is an integer
    # branch count); keep free >= 1 (a straight-line function must normalize to 0) and enforce the
    # _saturate precondition saturation > free.
    free_ts = max(1, round(free_ts_raw))
    sat_ts = max(free_ts + 1, round(sat_ts_raw))
    return {
        "method": "endpoint-percentile-match",
        "python_calibration": [py_free, py_sat],
        "anchor_percentiles": {
            "p_free": round(p_free, 4),
            "p_sat": round(p_sat, 4),
            "note": "p_free = P(py_cc <= 1); p_sat = P(py_cc <= 21)",
        },
        "typescript_calibration_raw": [round(free_ts_raw, 3), round(sat_ts_raw, 3)],
        "typescript_calibration": [free_ts, sat_ts],
    }


def _fairness_check(
    python_ccs: list[int], ts_ccs: list[int], ts_calibration: list[int]
) -> list[dict[str, object]]:
    """Sanity table: at a spread of percentiles, the Python-band and TS-band normalized scores
    should be close (the anchors match by construction; the interior shows the shape agreement)."""
    py_free, py_sat = PYTHON_COMPLEXITY_CALIBRATION
    ts_free, ts_sat = ts_calibration
    py_sorted = [float(v) for v in sorted(python_ccs)]
    ts_sorted = [float(v) for v in sorted(ts_ccs)]
    rows: list[dict[str, object]] = []
    for p in (0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99):
        py_cc = _quantile(py_sorted, p)
        ts_cc = _quantile(ts_sorted, p)
        rows.append(
            {
                "percentile": p,
                "py_cc": round(py_cc, 2),
                "ts_cc": round(ts_cc, 2),
                "py_norm": round(_saturate(py_cc, py_free, py_sat), 2),
                "ts_norm": round(_saturate(ts_cc, ts_free, ts_sat), 2),
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--cache", type=Path, default=TS_CACHE)
    args = parser.parse_args()

    repos = _load_manifest(args.manifest)
    print(f"TS corpus: {len(repos)} repos; cloning/analyzing...")
    ts_ccs, ts_provenance = _ts_ccs(repos, cache=args.cache)
    print(f"  {len(ts_ccs)} TS functions")
    print("Python corpus: analyzing cloned repos...")
    python_ccs, py_provenance = _python_ccs()
    print(f"  {len(python_ccs)} Python functions")

    result = derive(python_ccs, ts_ccs)
    result["typescript_distribution"] = _distribution(ts_ccs)
    result["python_distribution"] = _distribution(python_ccs)
    result["fairness_check"] = _fairness_check(python_ccs, ts_ccs, result["typescript_calibration"])  # type: ignore[arg-type]
    result["typescript_corpus"] = ts_provenance
    result["python_corpus"] = py_provenance

    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    band = result["typescript_calibration"]
    print(f"\nTYPESCRIPT_COMPLEXITY_CALIBRATION = ({band[0]}, {band[1]})")  # type: ignore[index]
    print(f"  raw: {result['typescript_calibration_raw']}  anchors: {result['anchor_percentiles']}")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
