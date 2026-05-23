# riskratchet

A maintainability ratchet for AI-assisted Python.

riskratchet computes a function-level risk score from coverage gaps,
cyclomatic complexity, churn, public surface, and sprawl signals. Snapshot
the current state as a baseline, then fail CI or block the commit whenever
risk grows. The bar can only move down, never up.

## Why CRAP alone is useful but incomplete

The classic CRAP score (`CC^2 * (1 - line_coverage)^3 + CC`) is great at
catching one specific shape of bad code: complex *and* poorly tested.
That's a real problem, but it misses several others that ship to production
just as often:

- A function with low complexity that has zero tests because no one wrote
  any. CRAP gives it `CC` (a single digit). Risk is real but not visible.
- A function with no missing line coverage but every branch covered the
  same way. CRAP only looks at line coverage.
- A function in a 2,000-line module that everyone is afraid to touch.
  Sprawl is invisible to CRAP.
- A function that changed in 40 of the last 90 commits. Churn is invisible
  to CRAP.

riskratchet keeps CRAP as a reported metric (it's still useful as a
single-number signal) and computes its own composite score from six
weighted components so those other risks show up too.

## Why AI-assisted workflows need a ratchet

AI coding agents are very good at producing code that compiles, runs, and
passes the tests it ships with. They are less good at:

- writing meaningful tests for the new code
- noticing when a 30-line function quietly became 130 lines
- catching that the public API now exposes a function with no callers in
  the tests
- realising that a small refactor turned an `if` ladder into a 14-way
  cyclomatic monster

A traditional review catches some of this. A ratchet catches all of it,
mechanically, every time. It pairs well with AI-assisted work because it
turns "did this change introduce risk?" into a yes/no question with a
diffable baseline.

## Quickstart

```bash
# install
pip install riskratchet
# or run directly without installing
uvx riskratchet --help
```

```bash
# 1. run your tests with coverage in JSON form
pytest --cov --cov-report=json:coverage.json

# 2. snapshot the current risk profile
riskratchet baseline src --coverage coverage.json --output .riskratchet.json

# 3. inspect what was captured
riskratchet scan src --coverage coverage.json

# 4. fail the build when risk regresses
riskratchet check src --coverage coverage.json --baseline .riskratchet.json
```

`riskratchet check` exits with code 1 when any regression is detected,
exit 2 for usage errors (e.g. missing baseline), and 0 otherwise.

## Pre-commit integration

Add this to `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/KayhanB21/riskratchet
    rev: v0.2.0
    hooks:
      - id: riskratchet
        args: ["src", "--coverage", "coverage.json", "--baseline", ".riskratchet.json"]
```

The hook expects you to have generated `coverage.json` already in your
pre-commit pipeline. Wire a `pytest --cov` step before riskratchet, or
generate it once locally and skip the hook stage on machines that don't
have coverage data.

## Pytest plugin

riskratchet ships a pytest plugin that runs `check` as part of your test
session. After `pip install riskratchet`:

```bash
pytest \
  --cov --cov-report=json:coverage.json \
  --riskratchet \
  --riskratchet-paths src \
  --riskratchet-baseline .riskratchet.json
```

The session exits non-zero when riskratchet finds regressions, so you can
gate CI on `pytest` alone. Available flags:

- `--riskratchet` (required to enable the plugin)
- `--riskratchet-paths` (default: `src`, repeatable)
- `--riskratchet-baseline` (default: `.riskratchet.json`)
- `--riskratchet-coverage` (default: `coverage.json`)
- `--riskratchet-fail-new-above` (default: `50`)
- `--riskratchet-fail-regression-above` (default: `5`)

## How risk is scored

Each function gets six component scores in `[0, 100]`:

| Component             | Weight | What it measures                                      |
| --------------------- | ------ | ----------------------------------------------------- |
| coverage_gap          | 30%    | `1 - line_coverage`                                   |
| structural_complexity | 25%    | cyclomatic complexity, saturating at CC=20            |
| branch_gap            | 15%    | `1 - branch_coverage` when branch coverage is known   |
| churn                 | 10%    | commits in the last 90 days, saturating at 10         |
| public_surface        | 10%    | coverage gap penalised harder when the function is public |
| sprawl                | 10%    | function length and file length blended               |

The total risk is the weighted sum. Severity bands: 0-24 low, 25-49
medium, 50-74 high, 75-100 critical.

## Output formats

```bash
riskratchet scan src --coverage coverage.json --format table     # default
riskratchet scan src --coverage coverage.json --format json
riskratchet scan src --coverage coverage.json --format markdown  # for PR comments
```

JSON output (truncated):

```json
{
  "summary": {
    "function_count": 42,
    "high_or_critical": 3,
    "average_score": 18.4
  },
  "functions": [
    {
      "path": "src/foo.py",
      "qualname": "Foo.bar",
      "score": 62.3,
      "severity": "high",
      "components": {
        "coverage_gap": 80.0,
        "structural_complexity": 55.0,
        "branch_gap": 70.0,
        "churn": 30.0,
        "public_surface": 80.0,
        "sprawl": 10.0
      },
      "crap": 12.4
    }
  ]
}
```

Markdown output is suitable for posting as a PR comment via `gh pr
comment`.

## Comparison with other tools

| Tool         | Per-function risk | Baseline / ratchet | Combines complexity + coverage + churn |
| ------------ | ----------------- | ------------------ | -------------------------------------- |
| coverage.py  | line / branch only| no                 | no                                     |
| radon        | complexity only   | no                 | no                                     |
| xenon        | complexity only   | yes (threshold)    | no                                     |
| pytest-crap  | yes (CRAP)        | no                 | partial (CC + line coverage)           |
| riskratchet  | yes               | yes                | yes                                    |

## Local development

```bash
uv sync
uv run ruff check .
uv run mypy src tests
uv run pytest --cov
uv run riskratchet scan src --coverage coverage.json
```

See `PLAN.md` for the v1 roadmap and `TODO.md` for the punch list.
