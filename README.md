<p align="center">
  <img src="assets/logo.png" alt="riskratchet logo" width="180">
</p>

# riskratchet

A maintainability ratchet for AI-assisted Python.

[PyPI](https://pypi.org/project/riskratchet/) · [Source](https://github.com/KayhanB21/riskratchet) · [Post](https://kayhan.dev/posts/014-letting-agents-write-code-without-ratcheting-up-risk/)

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

The published hook ships with `--no-auto-cov --allow-missing-coverage` by
default. pre-commit runs each hook in an isolated environment without your
project's pytest, so auto-coverage would usually fail there. Pick one of the
two patterns below.

### Pattern A: pre-generate coverage in a separate hook (recommended)

```yaml
repos:
  - repo: local
    hooks:
      - id: pytest-cov
        name: pytest --cov (produces coverage.json for riskratchet)
        entry: pytest --cov --cov-branch --cov-report=json:coverage.json -q
        language: system
        pass_filenames: false
        always_run: true

  - repo: https://github.com/KayhanB21/riskratchet
    rev: v0.2.0
    hooks:
      - id: riskratchet
        args:
          - "src"
          - "--coverage"
          - "coverage.json"
          - "--baseline"
          - ".riskratchet.json"
```

riskratchet uses the freshly produced `coverage.json` directly, no auto-cov
needed. The `pytest-cov` hook also catches test failures early.

### Pattern B: let riskratchet run pytest itself

Only viable if pre-commit's hook environment can find `pytest`. The simplest
way is `language: system` instead of `python`, so the hook inherits the
user's shell PATH:

```yaml
repos:
  - repo: local
    hooks:
      - id: riskratchet
        entry: riskratchet check src --baseline .riskratchet.json
        language: system
        pass_filenames: false
        always_run: true
```

riskratchet will run the configured `[tool.riskratchet] test_command`
(default `pytest --cov --cov-branch --cov-report=json:{output} -q`) and
cache the result under `.riskratchet/coverage.json`. The cache is reused
until any `.py` file under the scan paths is newer.

For local development outside pre-commit, the auto-coverage default applies
to plain `riskratchet scan|baseline|check` invocations as well; pass
`--no-auto-cov` to opt out.

## Using riskratchet from an AI coding agent

riskratchet is designed to be called from agents and parsed without
screen-scraping. See [`AGENTS.md`](AGENTS.md) for the full operational
contract; the recipes below cover the common cases.

One-shot: list the top three highest-risk functions.

```bash
riskratchet scan src --coverage coverage.json --json \
  | jq '.functions[:3] | .[] | {qualname, score, severity}'
```

Show the full baseline diff, including improvements and removed functions.

```bash
riskratchet diff src --coverage coverage.json \
  --baseline .riskratchet.json --json
```

Gate a CI job on regressions, printing the list when it fails.

```bash
riskratchet check src \
  --coverage coverage.json \
  --baseline .riskratchet.json \
  --baseline-format riskratchet \
  --json > regressions.json
status=$?
if [ "$status" -eq 1 ]; then
  jq -r '.regressions[] | "- \(.qualname): \(.reason)"' regressions.json
  exit 1
fi
exit "$status"
```

Post regressions as a PR comment.

```bash
riskratchet check src --coverage coverage.json \
  --baseline .riskratchet.json --format markdown \
  | gh pr comment --body-file -
```

For a sticky PR-bot body, use `--format pr-comment`. The output starts with
`<!-- riskratchet-report -->` so a GitHub Actions script can update an
existing comment instead of posting duplicates. For inline workflow warnings,
use `--format github`.

JSON output is validated against the schemas under
[`schemas/`](schemas/) on every release:

- `schemas/report.schema.json` — `scan --json`
- `schemas/regressions.schema.json` — `check --json`
- `schemas/diff.schema.json` — `diff --json`
- `schemas/baseline.schema.json` — `.riskratchet.json` on disk

Native JSON output includes `$schema` and `version` fields so consumers can
pin parsing behavior.

### Common mistakes

- Running `check` without a baseline. `riskratchet baseline` must run first
  (typically on `main`) and the resulting `.riskratchet.json` checked in.
  Exits with code `2` when missing.
- Passing `coverage.xml` to `--coverage`. riskratchet reads
  `coverage.json`. Generate it with `pytest --cov --cov-report=json:coverage.json`
  or let riskratchet auto-generate it (see Pre-commit integration).
- Relying on the auto-coverage runner inside a sandbox with no pytest
  installed. Pass `--no-auto-cov` plus `--allow-missing-coverage`, or set
  `[tool.riskratchet] test_command` to a runner that does work in your
  environment.
- Running without `--no-git` inside a sandbox that has no git history. Churn
  collection will be empty rather than failing, but pass `--no-git` to be
  explicit and slightly faster.
- Parsing stdout as both prose and JSON. Pick a format. With `--json`,
  stdout is a single JSON object; status messages go to stderr.
- Bumping the baseline to silence a regression. The baseline is the bar; if
  it has to move up, do it in a dedicated PR with a written justification.

### Suppressions and partial coverage

Use `--exclude` to skip files at discovery time. Use `--allow` to analyze a
file but suppress matching functions from reporting and gating:

```bash
riskratchet check src --baseline .riskratchet.json \
  --allow "GeneratedModel.*" \
  --allow "src/generated/**"
```

Function patterns match dotted qualified names. Patterns containing `/` or
`**` match repo-relative POSIX paths.

When a coverage file is present but a scanned source file has no matching
coverage entry, riskratchet warns on stderr. The default missing-coverage
policy is pessimistic: treat those functions as uncovered. For partial local
runs you can choose:

```bash
riskratchet scan src --coverage coverage.json --missing-coverage optimistic
riskratchet scan src --coverage coverage.json --missing-coverage skip
```

`optimistic` treats missing file coverage as fully covered. `skip` drops
functions from unmapped files and reports the skipped count in JSON summary.

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

Weights are configurable per project. Drop a `[tool.riskratchet.weights]`
table in `pyproject.toml` to override any subset; the remaining
components keep their defaults and the whole vector is renormalized so the
total still maps to `[0, 100]`. For example, to ignore churn entirely and
double-weight coverage:

```toml
[tool.riskratchet.weights]
coverage_gap = 0.6
churn = 0.0
```

Unknown keys and negative values are rejected at startup so a typo cannot
silently weaken the score.

## Output formats

```bash
riskratchet scan src --coverage coverage.json --format table     # default
riskratchet scan src --coverage coverage.json --json             # shortcut for --format json
riskratchet scan src --coverage coverage.json --format markdown  # for PR comments
riskratchet scan src --coverage coverage.json --format sarif     # for SARIF consumers
riskratchet scan src --coverage coverage.json --format github    # GitHub Actions annotations
riskratchet scan src --coverage coverage.json --format pr-comment
riskratchet scan src --coverage coverage.json --quiet            # drops the trailing summary line
riskratchet scan src --coverage coverage.json --min-score 50     # hide lower-risk functions
riskratchet scan src --coverage coverage.json --top 10           # emit only the top N
```

`riskratchet check` accepts `--baseline-format riskratchet`, which is the
default and currently the only supported baseline format.

For early adoption before a baseline exists, `scan` can also fail on an
absolute gate:

```bash
riskratchet scan src --coverage coverage.json --fail-above 75
riskratchet scan src --coverage coverage.json --fail-severity high
```

Baseline gating is still the recommended mode for mature codebases.

JSON output (truncated):

```json
{
  "$schema": "https://github.com/KayhanB21/riskratchet/schemas/report.schema.json",
  "version": "0.2",
  "summary": {
    "total_functions": 10,
    "analyzed_functions": 42,
    "emitted_functions": 10,
    "total_files": 6,
    "coverage_status": "present",
    "suppressed_functions": 1,
    "skipped_missing_coverage": 0,
    "by_severity": {
      "low": 1,
      "medium": 6,
      "high": 3,
      "critical": 0
    }
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

## Editor integration

See [`docs/ide-integration.md`](docs/ide-integration.md) for how to view
findings inline in VS Code (via the SARIF Viewer extension) and JetBrains
IDEs.

## Sample output on real libraries

I ran riskratchet against four widely-used Python libraries to show
what its output looks like on production code. Each was cloned fresh,
its own test suite run with `pytest --cov --cov-report=json:coverage.json`,
then scanned. Top findings:

| Library | Function | Score | CC | Line cov |
| --- | --- | ---: | ---: | ---: |
| python-slugify | `__main__::main` | 53.1 (high) | 3 | 11% (0% branch) |
| python-slugify | `slugify` | 33.3 | 27 | 88% |
| tabulate | `_CustomTextWrap._wrap_chunks` | 44.4 | 31 | 60% |
| tabulate | `_normalize_tabular_data` | 42.6 | 76 | 78% |
| tabulate | `tabulate` (entry) | 37.1 | 62 | 97% |
| humanize | `precisedelta` | 32.9 | 26 | 100% |
| humanize | `naturaldelta` | 32.4 | 33 | 100% |
| inflect | `engine._sinoun` | 36.7 | 108 | 98% |
| inflect | `engine._plnoun` | 36.2 | 100 | 99% |

The point is not that these libraries are bad. They have all-green CI
and many users. The point is that even mature, well-tested code
accumulates functions where complexity, coverage, and sprawl combine
into something worth a second pair of eyes. A CC=108 function with 98%
coverage is not on fire. It is a function that works and is tested. The
ratchet's job is to keep those numbers from getting worse over time.

## Comparison with other tools

| Tool         | Per-function risk | Baseline / ratchet | Combines complexity + coverage + churn |
| ------------ | ----------------- | ------------------ | -------------------------------------- |
| coverage.py  | line / branch only| no                 | no                                     |
| radon        | complexity only   | no                 | no                                     |
| xenon        | complexity only   | yes (threshold)    | no                                     |
| pytest-crap  | yes (CRAP)        | no                 | partial (CC + line coverage)           |
| riskratchet  | yes               | yes                | yes                                    |

## Local development

The same commands run in GitHub Actions. Run them locally before pushing.

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest --cov=src/riskratchet --cov-branch --cov-report=term-missing
uv build --clear
```

Typing `tests/` is on the roadmap but not yet enforced; CI only runs
`mypy src`.

## Release

CI validates every push and pull request. Publishing is still manual.

```bash
./scripts/publish.sh
```

The script runs the same gates CI does, then `uv build` and `twine
upload`. The GitHub Actions workflow under `.github/workflows/` is the
source of truth for what "green" means; if a check fails there, do not
ship.
