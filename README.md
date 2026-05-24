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

## Use cases

Four real scenarios for how riskratchet earns its keep.

### 1. Solo dev with an AI agent on a year-old side project

You've been vibe-coding a FastAPI backend with an AI agent for eight
months. It works, tests are green-ish (62% coverage), but you just
noticed that `services/billing.py::reconcile_subscriptions` quietly grew
to 180 lines and an 11-way `match` statement you don't remember writing.

```bash
pip install riskratchet
pytest --cov --cov-branch --cov-report=json:coverage.json
riskratchet scan src --coverage coverage.json --top 10
```

`reconcile_subscriptions` shows up at score 71 (high) with
`structural_complexity: 90`, `sprawl: 55`, `coverage_gap: 60`. You also
spot a surprise: a 12-line public utility `_normalize_plan_id` scoring
48 because it has zero tests. Snapshot the bar:

```bash
riskratchet baseline src --coverage coverage.json --output .riskratchet.json
git add .riskratchet.json && git commit -m "Add riskratchet baseline"
```

From here, every time the agent adds a webhook handler or "refactors the
billing flow," run `riskratchet check` before committing. If it quietly
bloated `reconcile_subscriptions` from 180 to 220 lines, the check exits
1 and names the regression. You stop having to remember to look.

**Why this is the canonical use case:** AI agents are excellent at
adding code, mediocre at noticing they've made things worse. The
baseline is your memory.

### 2. Team gating PRs in CI

Five engineers maintain an internal Python SDK that 30 other services
consume. Coverage is 87%, reviews are decent, but PRs occasionally land
functions with CC > 25 because reviewers don't catch it. One-time
setup on `main`:

```bash
pytest --cov --cov-branch --cov-report=json:coverage.json
riskratchet baseline src --coverage coverage.json --output .riskratchet.json
git add .riskratchet.json
```

In GitHub Actions, on every PR:

```yaml
- run: pytest --cov --cov-branch --cov-report=json:coverage.json
- run: |
    riskratchet check src \
      --coverage coverage.json \
      --baseline .riskratchet.json \
      --format pr-comment > regressions.md
    status=$?
    if [ $status -eq 1 ]; then
      gh pr comment ${{ github.event.pull_request.number }} --body-file regressions.md
      exit 1
    fi
```

Tune `pyproject.toml` to reflect priorities — this SDK changes
constantly by design, so churn matters less than public surface:

```toml
[tool.riskratchet.weights]
churn = 0.0
public_surface = 0.20
```

The `pr-comment` format starts with `<!-- riskratchet-report -->`, so
the bot updates the same comment on each push instead of spamming.

**Why this works for teams:** the ratchet is mechanical and unowned.
Nobody has to be "the complexity cop" in code review.

### 3. Pre-commit hook for a solo repo with no CI

A data scientist has a `pipelines/` repo. No CI, no PR review — just one
person pushing to `main`. They want a local guardrail before each
commit. Using Pattern A from the [Pre-commit integration](#pre-commit-integration)
section:

```yaml
repos:
  - repo: local
    hooks:
      - id: pytest-cov
        entry: pytest --cov --cov-branch --cov-report=json:coverage.json -q
        language: system
        pass_filenames: false
        always_run: true
  - repo: https://github.com/KayhanB21/riskratchet
    rev: v0.2.0
    hooks:
      - id: riskratchet
        args: ["pipelines", "--coverage", "coverage.json", "--baseline", ".riskratchet.json"]
```

After the initial `riskratchet baseline pipelines …`, every `git
commit` regenerates coverage and gates the commit on no regressions. If
they try to commit a 90-line transform with no tests, the commit fails
with a list of regressed functions. They can use `--allow
"pipelines.experiments.*"` to scope out an experimental folder where
rough code is intentional.

**Why this matters here:** without a team, there's no second pair of
eyes. The hook *is* the review.

### 4. Investigating one ugly function with `explain` and `diff`

An engineer is assigned the dreaded `inflect.engine._plnoun` (CC=100,
from the [Sample output](#sample-output-on-real-libraries) section
below). They want to know why riskratchet flagged it and whether their
planned refactor actually helps.

```bash
riskratchet explain src --coverage coverage.json --qualname "engine._plnoun"
```

This dumps the six component scores, the CRAP value, line numbers, and
what's driving the risk — e.g. `structural_complexity: 100, sprawl: 78,
coverage_gap: 2`. Now they know the problem isn't tests; it's the
function shape.

They branch, spend two days breaking `_plnoun` into seven smaller
functions, then before opening the PR:

```bash
riskratchet diff src --coverage coverage.json --baseline .riskratchet.json --json \
  | jq '.improved[], .regressed[]'
```

`diff` shows improvements as well as regressions. `_plnoun` dropped from
36.2 → 18.4, and the seven new helpers all score under 20. They paste
that into the PR description as evidence that the refactor was
net-positive — not just rearranging deck chairs.

**Why this scenario matters:** `scan` tells you *what's* risky,
`explain` tells you *why*, `diff` tells you whether your change helped.

---

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

### How pre-commit and riskratchet fit together

Two things about pre-commit matter for riskratchet:

1. **Pre-commit hides your unstaged edits before running hooks.** It
   "stashes" anything you've edited but not `git add`-ed, so hooks only
   see the code you're actually about to commit. Useful in general, but it
   means riskratchet sees a different source tree than the one open in
   your editor.
2. **Each `language: python` hook runs in its own isolated virtualenv.**
   That venv contains riskratchet and its declared dependencies — *not*
   your project's pytest, your application code, your fixtures, or your
   test plugins. So riskratchet can't simply "run your tests" from inside
   the hook environment; pytest there would fail to import your package.

Together these create one requirement: **the `coverage.json` riskratchet
reads must reflect the same stashed source tree it's analyzing.** If you
reuse an old `coverage.json` from before pre-commit stashed your edits,
the source and coverage drift out of sync — you may see phantom
"uncovered" lines for code that no longer exists, or score functions
against the wrong line ranges.

That's why the published hook ships with `--no-auto-cov
--allow-missing-coverage` by default: it's safe but limited, and assumes
you'll wire coverage in yourself. Pick one of the two patterns below to
make it actually useful.

### Pattern A: pre-generate coverage in a separate hook (recommended)

Add a sibling hook that runs `pytest --cov` *inside the same pre-commit
chain*. Because that hook runs after pre-commit has already stashed
unstaged edits, the coverage it produces matches the stashed source tree
exactly — riskratchet then reads a consistent picture.

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

To escape the isolated venv, override the hook to `language: system` so
it inherits your shell PATH (and finds your real pytest, your project,
and its deps):

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

### Components, in plain English

Each component is rescaled to `[0, 100]` (where 100 = maximum risk for
that signal) before being weighted into the total. Here's what each one
actually *means*, with a concrete example.

**`coverage_gap` — "is this function tested at all?"**
The fraction of lines in the function that your test suite never
executes. A function with 100% line coverage scores 0; a function with
0% line coverage scores 100.

> *Example:* a 40-line `parse_invoice` where your tests only exercise
> the happy path (28 lines covered, 12 missed) → `coverage_gap = 30`.
> A brand-new `migrate_to_v2` with no tests at all → `coverage_gap = 100`.

**`structural_complexity` — "how many ways can this function go?"**
Cyclomatic complexity, which roughly counts independent paths through
the function (each `if`, `elif`, `and`, `or`, `for`, `except` adds
one). Saturates at CC=20 — anything past that is already "very
complex" and we don't need to keep counting.

> *Example:* a getter with one return statement → `CC=1`, score 0.
> A `validate_user_input` with 6 chained `if/elif` branches → `CC=7`,
> score ~35. A 14-way `match` statement → `CC=15`, score ~75.

**`branch_gap` — "are both sides of every `if` tested?"**
Like `coverage_gap`, but for branches. A function whose tests only ever
take the `if True` path of an `if/else` will have full line coverage
but only 50% branch coverage. Only counts when your coverage run
included `--cov-branch`.

> *Example:* `def discount(user): return 0.2 if user.is_premium else 0.0`.
> A test that only passes premium users → 100% line coverage but 50%
> branch coverage → `branch_gap = 50`.

**`churn` — "how often does this function change?"**
Number of git commits touching the function's line range in the
configured churn window (default 90 days, set with `--churn-days` or
`[tool.riskratchet] churn_window_days`). Saturates at 10 commits.
High churn means many people have edited it recently, which correlates
with bugs.

> *Example:* a stable `parse_iso_date` last touched two years ago →
> `churn = 0`. A `pricing_engine.calculate_total` that's been edited
> in 14 of the last 90 commits → saturates at 10 → `churn = 100`.

**`public_surface` — "if this breaks, do callers we can't see break too?"**
A multiplier on coverage gap: when a function is part of your public
API (no leading underscore, importable from a package's `__init__`),
its missing coverage is penalized harder than the same gap on a private
helper. A private helper with 40% coverage is a problem you can fix
locally; a public function with 40% coverage is a contract problem.

> *Example:* `_normalize_path` with 50% coverage → `public_surface = 25`.
> Public `format_currency` with 50% coverage → `public_surface = 50`.

**`sprawl` — "is this function (or its file) just too big?"**
A blend of function length and the surrounding file's length. Long
functions are harder to hold in your head; long files mean any function
in them has more neighbors competing for attention. Both contribute.

> *Example:* a 12-line function in a 200-line file → `sprawl = 5`.
> A 180-line function in a 2,000-line module → `sprawl = 85`.

### Putting it together: a worked example

Suppose `services/billing.py::reconcile_subscriptions` is 180 lines,
public, has CC=14, 55% line coverage, 40% branch coverage, no recent
churn, and lives in a 900-line file. Its components might look like:

| Component             | Raw signal               | Score | Weight | Contribution |
| --------------------- | ------------------------ | ----: | -----: | -----------: |
| coverage_gap          | 45% uncovered            |    45 |   0.30 |         13.5 |
| structural_complexity | CC=14 of 20 saturating   |    70 |   0.25 |         17.5 |
| branch_gap            | 60% uncovered branches   |    60 |   0.15 |          9.0 |
| churn                 | 0 commits in 90 days     |     0 |   0.10 |          0.0 |
| public_surface        | public + 45% gap         |    45 |   0.10 |          4.5 |
| sprawl                | long function, big file  |    65 |   0.10 |          6.5 |
| **total**             |                          |       |        |     **51.0** |

Score 51 → **high** severity. The dominant drivers are complexity and
branch coverage; if you wanted to lower it without rewriting the
function, the cheapest path is adding branch tests, not deleting lines.

### Configuring weights

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
