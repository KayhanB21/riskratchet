<p align="center">
  <img src="https://raw.githubusercontent.com/KayhanB21/riskratchet/master/assets/logo.png" alt="riskratchet logo" width="180">
</p>

# riskratchet

**A maintainability ratchet for AI-assisted Python.** The bar can only move down.

[PyPI](https://pypi.org/project/riskratchet/) · [Source](https://github.com/KayhanB21/riskratchet) · [Post](https://kayhan.dev/posts/014-letting-agents-write-code-without-ratcheting-up-risk/)

AI coding agents are very good at writing code that compiles, runs, and passes
the tests they ship with it. They are less good at:

- writing meaningful tests for the new code,
- noticing a 30-line function quietly became 130 lines,
- catching that the public API now exposes a function with no callers in tests,
- realising a small refactor turned an `if` ladder into a 14-way cyclomatic monster.

A traditional review catches some of this. A ratchet catches all of it,
mechanically, every time. riskratchet computes a per-function risk score from
coverage gaps, cyclomatic complexity, churn, public surface, and sprawl, then
fails CI or blocks the commit whenever risk grows past a baseline. Nobody has to
play complexity cop.

The review workflow is inspired by
[`cargo-crap`](https://github.com/minikin/cargo-crap) (which made the CRAP
metric practical in CI with baselines, PR comments, and JSON output) and
Cursor's [`thermo-nuclear-code-quality-review`](https://github.com/cursor/plugins/blob/main/cursor-team-kit/agents/thermo-nuclear-code-quality-review.md)
agent prompt (which emphasises maintainability, structure, sprawl, and
explicit boundaries). riskratchet is neither a Python port of cargo-crap nor an
agent prompt: it reports CRAP and adds Python-specific signals on top (branch
gaps, churn, public surface, sprawl).

## Quickstart

```bash
pip install riskratchet
# or run without installing
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

`riskratchet check` exits `1` on regressions, `2` on usage errors (e.g. missing
baseline), and `0` otherwise.

For early adoption before a baseline exists, `scan` can also fail on an
absolute gate (baseline gating remains the recommended mode for mature
codebases):

```bash
riskratchet scan src --coverage coverage.json --fail-above 75
riskratchet scan src --coverage coverage.json --fail-severity high
```

## The canonical use case: AI agent + side project

You've been vibe-coding a FastAPI backend with an AI agent for eight months.
It works, tests are green-ish (62% coverage), but you just noticed
`services/billing.py::reconcile_subscriptions` quietly grew to 180 lines and
an 11-way `match` statement you don't remember writing.

```bash
pip install riskratchet
pytest --cov --cov-branch --cov-report=json:coverage.json
riskratchet scan src --coverage coverage.json --top 10
```

`reconcile_subscriptions` shows up at score 71 (high) with
`structural_complexity: 90`, `sprawl: 55`, `coverage_gap: 60`. You also spot a
surprise: a 12-line public utility `_normalize_plan_id` scoring 48 because it
has zero tests. Snapshot the bar:

```bash
riskratchet baseline src --coverage coverage.json --output .riskratchet.json
git add .riskratchet.json && git commit -m "Add riskratchet baseline"
```

From here, every time the agent adds a webhook handler or "refactors the
billing flow," run `riskratchet check` before committing. If it quietly bloated
`reconcile_subscriptions` from 180 to 220 lines, the check exits `1` and names
the regression. You stop having to remember to look.

**Why this is the canonical use case:** AI agents are excellent at adding
code, mediocre at noticing they've made things worse. The baseline is your
memory.

### Other patterns

- **Team gating PRs in CI.** Run `pytest --cov` and `riskratchet check --format pr-comment`
  in GitHub Actions; pipe to `gh pr comment`. The PR-comment format starts with
  `<!-- riskratchet-report -->` so the bot updates the same comment on each push
  instead of spamming. The ratchet is mechanical and unowned, so nobody has to
  play "the complexity cop" in code review. See
  [Using riskratchet from an AI coding agent](#using-riskratchet-from-an-ai-coding-agent).
- **Pre-commit hook for a solo repo.** Wire `pytest-cov` and `riskratchet`
  into `.pre-commit-config.yaml` so every commit regenerates coverage and
  gates the commit on no regressions. See
  [Pre-commit integration](#pre-commit-integration).
- **Investigating one ugly function.** Use `riskratchet explain
  path/to/file.py::qualname` to dump the six component scores and find the
  driver (complexity vs. coverage vs. sprawl). After refactoring, run
  `riskratchet diff --json | jq '.improved[], .regressed[]'` to prove the
  change was net-positive, not just rearranging deck chairs.

## Why CRAP alone is useful but incomplete

The classic CRAP score (`CC^2 * (1 - line_coverage)^3 + CC`) catches one
shape of bad code: complex *and* poorly tested. That's a real problem, but
it misses several others that ship to production just as often:

- A function with low complexity and zero tests. CRAP gives it `CC` (a single
  digit). Risk is real but invisible.
- A function with full line coverage but every branch covered the same way.
  CRAP only looks at line coverage.
- A function in a 2,000-line module everyone is afraid to touch. Sprawl is
  invisible to CRAP.
- A function that changed in 40 of the last 90 commits. Churn is invisible
  to CRAP.

riskratchet keeps CRAP as a reported metric and computes its own composite
score from six weighted components so those other risks show up too.

## Pre-commit integration

### How pre-commit and riskratchet fit together

Two things about pre-commit matter for riskratchet:

1. **Pre-commit hides your unstaged edits before running hooks.** Hooks only
   see the code you're actually about to commit. Useful in general, but it
   means riskratchet sees a different source tree than the one open in your
   editor.
2. **Each `language: python` hook runs in its own isolated virtualenv** that
   contains riskratchet and its declared deps, *not* your project's pytest,
   application code, or test plugins.

Together these create one requirement: **the `coverage.json` riskratchet reads
must reflect the same stashed source tree it's analyzing.** Reusing an old
`coverage.json` from before pre-commit stashed your edits drifts source and
coverage out of sync.

That's why the published hook ships with `--no-auto-cov
--allow-missing-coverage` by default: safe but limited. Pick one of the
patterns below to make it useful.

### Pattern A: pre-generate coverage in a sibling hook (recommended)

Run `pytest --cov` *inside the same pre-commit chain* so the coverage matches
the stashed tree exactly.

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
    rev: v0.2.4
    hooks:
      - id: riskratchet
        args:
          - "src"
          - "--coverage"
          - "coverage.json"
          - "--baseline"
          - ".riskratchet.json"
```

#### Variant: uv / poetry projects (all `language: system`)

Skip the isolated venv entirely and run both hooks inside your project's
environment. This is what riskratchet itself uses:

```yaml
repos:
  - repo: local
    hooks:
      - id: pytest-cov
        entry: uv run pytest --cov --cov-branch --cov-report=json:coverage.json -q
        language: system
        pass_filenames: false
        always_run: true

      - id: riskratchet
        entry: uv run riskratchet check src --coverage coverage.json --baseline .riskratchet.json --no-auto-cov
        language: system
        pass_filenames: false
        always_run: true
```

Two upsides: single env for both hooks (no isolated-venv surprises), and
`uv run` resolves the same Python and deps `uv sync` set up. Downside:
contributors must have `uv` installed locally.

### Pattern B: let riskratchet run pytest itself

Override the hook to `language: system` so it inherits your shell PATH (and
finds your real pytest):

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

riskratchet runs the configured `[tool.riskratchet] test_command` (default
`pytest --cov --cov-branch --cov-report=json:{output} -q`) and caches the
result under `.riskratchet/coverage.json`. The cache is reused until any `.py`
file under the scan paths is newer.

For local development outside pre-commit, auto-coverage applies to plain
`riskratchet scan|baseline|check` too; pass `--no-auto-cov` to opt out.

## Using riskratchet from an AI coding agent

riskratchet is designed to be called from agents and parsed without
screen-scraping. See [`AGENTS.md`](AGENTS.md) for the full operational
contract; the recipes below cover the common cases.

Top three highest-risk functions:

```bash
riskratchet scan src --coverage coverage.json --json \
  | jq '.functions[:3] | .[] | {qualname, score, severity}'
```

Full baseline diff including improvements and removed functions:

```bash
riskratchet diff src --coverage coverage.json \
  --baseline .riskratchet.json --json
```

Gate a CI job on regressions:

```bash
riskratchet check src --coverage coverage.json \
  --baseline .riskratchet.json --json > regressions.json
status=$?
if [ "$status" -eq 1 ]; then
  jq -r '.regressions[] | "- \(.qualname): \(.reason)"' regressions.json
  exit 1
fi
exit "$status"
```

Post regressions as a PR comment (use `--format pr-comment` for a sticky body
that updates in place via the `<!-- riskratchet-report -->` marker; use
`--format github` for inline workflow warnings):

```bash
riskratchet check src --coverage coverage.json \
  --baseline .riskratchet.json --format markdown \
  | gh pr comment --body-file -
```

Markdown and PR-comment output can link each row back to source:

```bash
riskratchet scan src --format pr-comment \
  --repo-url https://github.com/acme/project \
  --commit-ref "$GITHUB_SHA"
```

In GitHub Actions, those values are filled from `GITHUB_SERVER_URL`,
`GITHUB_REPOSITORY`, and `GITHUB_SHA` when available.

JSON output is validated against the schemas under
[`schemas/`](schemas/) on every release:

- `report.schema.json`: `scan --json`
- `regressions.schema.json`: `check --json`
- `diff.schema.json`: `diff --json`
- `baseline.schema.json`: `.riskratchet.json` on disk
- `summary.schema.json`: `scan|check|diff --summary --json`
- `config.schema.json`: `config show --json`

Native JSON output includes `$schema` and `version` fields so consumers can
pin parsing behavior.

### Common pitfalls

- **Running `check` without a baseline.** `riskratchet baseline` must run
  first (typically on `main`) and the resulting `.riskratchet.json` checked
  in. Exits `2` when missing.
- **Passing `coverage.xml` to `--coverage`.** riskratchet reads
  `coverage.json`. Generate it with `pytest --cov --cov-report=json:coverage.json`.
- **Parsing stdout as both prose and JSON.** Pick a format. With `--json`,
  stdout is a single JSON object; status messages go to stderr. When `check`
  exits `1`, a short hint with the two escape hatches (regenerate baseline,
  or loosen the per-component gate) is written to stderr, so stdout stays
  clean.
- **Bumping the baseline to silence a regression.** The baseline is the bar;
  if it has to move up, do it in a dedicated PR with a written justification.
  In `check` output, "new" means **absent from the baseline**, so a function
  added in an earlier commit can still appear as new until the baseline
  intentionally accepts it.

For the broader trust boundaries and non-goals, see
[`docs/threat-model.md`](docs/threat-model.md).

### Suppressions and partial coverage

`--exclude` skips files at discovery time. `--allow` analyzes a file but
suppresses matching functions from reporting and gating:

```bash
riskratchet check src --baseline .riskratchet.json \
  --allow "GeneratedModel.*" \
  --allow "src/generated/**"
```

Function patterns match dotted qualified names. Patterns containing `/` or
`**` match repo-relative POSIX paths.

The default missing-coverage policy is pessimistic: unmapped functions are
treated as uncovered. For partial local runs:

```bash
riskratchet scan src --coverage coverage.json --missing-coverage optimistic
riskratchet scan src --coverage coverage.json --missing-coverage skip
```

`optimistic` treats missing file coverage as fully covered. `skip` drops
functions from unmapped files and reports the skipped count.

## Pytest plugin

riskratchet ships a pytest plugin that runs `check` as part of your test
session:

```bash
pytest \
  --cov --cov-report=json:coverage.json \
  --riskratchet \
  --riskratchet-paths src \
  --riskratchet-baseline .riskratchet.json
```

The session exits non-zero when riskratchet finds regressions, so CI can gate
on `pytest` alone. Available flags:

- `--riskratchet` (required to enable)
- `--riskratchet-paths` (default: `src`, repeatable)
- `--riskratchet-baseline` (default: `.riskratchet.json`)
- `--riskratchet-coverage` (default: `coverage.json`)
- `--riskratchet-fail-new-above` (default: `50`)
- `--riskratchet-fail-regression-above` (default: `5`)
- `--riskratchet-fail-existing-above` (default: unset)
- `--riskratchet-fail-component-regression-above` (default: `15`)
- `--riskratchet-no-component-regression-gate`

## How risk is scored

Each function gets six component scores in `[0, 100]`:

| Component             | Weight | What it measures                                          |
| --------------------- | ------ | --------------------------------------------------------- |
| coverage_gap          | 30%    | `1 - line_coverage`                                       |
| structural_complexity | 25%    | cyclomatic complexity, saturating at CC=20                |
| branch_gap            | 15%    | `1 - branch_coverage` when branch coverage is known       |
| churn                 | 10%    | commits in the last 90 days, saturating at 10             |
| public_surface        | 10%    | coverage gap penalised harder when the function is public |
| sprawl                | 10%    | function length and file length blended                   |

Total risk is the weighted sum. Severity bands: 0-24 low, 25-49 medium,
50-74 high, 75-100 critical.

`is_public` is determined statically from the AST: by qualname when no
`__all__` is declared (leading-underscore is private, dunders are public);
by additive promotion from a static `__all__` (omission never demotes);
fall back to the naming rule when `__all__` is dynamic. Full rules in
[`AGENTS.md`](AGENTS.md#is_public-classification).

### Components, in plain English

Each component is rescaled to `[0, 100]` (where 100 = maximum risk for that
signal) before being weighted into the total. Here's what each one actually
*means*, with a concrete example.

**`coverage_gap`: "is this function tested at all?"**
The fraction of lines in the function that your test suite never executes.
A function with 100% line coverage scores 0; a function with 0% line
coverage scores 100.

> *Example:* a 40-line `parse_invoice` where your tests only exercise the
> happy path (28 lines covered, 12 missed) gives `coverage_gap = 30`. A
> brand-new `migrate_to_v2` with no tests at all gives `coverage_gap = 100`.

**`structural_complexity`: "how many ways can this function go?"**
Cyclomatic complexity, which roughly counts independent paths through the
function (each `if`, `elif`, `and`, `or`, `for`, `except` adds one).
Saturates at CC=20; anything past that is already "very complex" and
there's no value in keeping count.

> *Example:* a getter with one return statement is `CC=1`, score 0. A
> `validate_user_input` with 6 chained `if/elif` branches is `CC=7`, score
> ~35. A 14-way `match` statement is `CC=15`, score ~75.

**`branch_gap`: "are both sides of every `if` tested?"**
Like `coverage_gap`, but for branches. A function whose tests only ever
take the `if True` path of an `if/else` will have full line coverage but
only 50% branch coverage. Only counts when your coverage run included
`--cov-branch`.

> *Example:* `def discount(user): return 0.2 if user.is_premium else 0.0`.
> A test that only passes premium users gets 100% line coverage but 50%
> branch coverage, so `branch_gap = 50`.

**`churn`: "how often does this function change?"**
Number of git commits touching the function's line range in the configured
churn window (default 90 days, set with `--churn-days` or `[tool.riskratchet]
churn_window_days`). Saturates at 10 commits. High churn means many people
have edited it recently, which correlates with bugs.

> *Example:* a stable `parse_iso_date` last touched two years ago is
> `churn = 0`. A `pricing_engine.calculate_total` edited in 14 of the last
> 90 commits saturates at 10, so `churn = 100`.

**`public_surface`: "if this breaks, do callers we can't see break too?"**
A multiplier on coverage gap: when a function is part of your public API,
its missing coverage is penalised harder than the same gap on a private
helper. A private helper with 40% coverage is a problem you can fix
locally; a public function with 40% coverage is a contract problem.

> *Example:* `_normalize_path` with 50% coverage gives `public_surface = 25`.
> Public `format_currency` with 50% coverage gives `public_surface = 50`.
> `_LegacyExposed` listed in `__all__` with 50% coverage gives
> `public_surface = 50` (promoted to public despite the underscore).

**`sprawl`: "is this function (or its file) just too big?"**
A blend of function length and the surrounding file's length. Long
functions are harder to hold in your head; long files mean any function in
them has more neighbors competing for attention. Both contribute.

> *Example:* a 12-line function in a 200-line file gives `sprawl = 5`. A
> 180-line function in a 2,000-line module gives `sprawl = 85`.

### A worked example

Suppose `services/billing.py::reconcile_subscriptions` is 180 lines, public,
has CC=14, 55% line coverage, 40% branch coverage, no recent churn, and
lives in a 900-line file. Its components might look like:

| Component             | Raw signal               | Score | Weight | Contribution |
| --------------------- | ------------------------ | ----: | -----: | -----------: |
| coverage_gap          | 45% uncovered            |    45 |   0.30 |         13.5 |
| structural_complexity | CC=14 of 20 saturating   |    70 |   0.25 |         17.5 |
| branch_gap            | 60% uncovered branches   |    60 |   0.15 |          9.0 |
| churn                 | 0 commits in 90 days     |     0 |   0.10 |          0.0 |
| public_surface        | public + 45% gap         |    45 |   0.10 |          4.5 |
| sprawl                | long function, big file  |    65 |   0.10 |          6.5 |
| **total**             |                          |       |        |     **51.0** |

Score 51 puts this in the **high** severity band. The dominant drivers are
complexity and branch coverage; if you wanted to lower it without rewriting
the function, the cheapest path is adding branch tests, not deleting lines.

### Configuring weights

Drop a `[tool.riskratchet.weights]` table in `pyproject.toml` to override any
subset; the remaining components keep their defaults and the whole vector is
renormalized. For example, to ignore churn entirely and double-weight
coverage:

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
riskratchet scan src --coverage coverage.json --summary          # aggregate lines only
riskratchet scan src --coverage coverage.json --summary --json   # schema-backed summary envelope
riskratchet scan src --coverage coverage.json --quiet            # drops the trailing summary line
riskratchet scan src --coverage coverage.json --min-score 50     # hide lower-risk functions
riskratchet scan src --coverage coverage.json --top 10           # emit only the top N
```

SARIF intentionally has a narrower contract than native JSON: `scan --format
sarif` emits current findings after the same score filter used for
annotations, while `check --format sarif` and `diff --format sarif` emit only
failing regressions. A clean baseline still produces valid SARIF with an
empty `results` array.

Native JSON output (truncated):

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
    "by_severity": { "low": 1, "medium": 6, "high": 3, "critical": 0 }
  },
  "functions": [
    {
      "path": "src/foo.py",
      "qualname": "Foo.bar",
      "score": 62.3,
      "severity": "high",
      "components": {
        "coverage_gap": 80.0, "structural_complexity": 55.0,
        "branch_gap": 70.0, "churn": 30.0,
        "public_surface": 80.0, "sprawl": 10.0
      },
      "crap": 12.4
    }
  ]
}
```

## Config validation, groups, and monorepos

Validate project config before relying on it in CI:

```bash
riskratchet config validate --config pyproject.toml
riskratchet config show --config pyproject.toml --json
```

`config validate` exits `2` for malformed TOML, unknown keys, invalid value
types, or invalid groups.

riskratchet finds config by walking upward from the working directory for the
nearest `pyproject.toml` containing `[tool.riskratchet]` (pass `--config` to
point at a specific file). Relative config paths (`paths`, `coverage`,
`baseline`, the coverage map, the coverage cache) resolve against that file's
directory, so running from a nested package directory gives the same result as
running from the project root; an explicit `--coverage` and positional path
arguments stay relative to your current directory. The scanning commands only
*warn* on an unknown `[tool.riskratchet]` key, so a config written for a newer
version still runs; reach for `config validate` when you want that typo to
fail (exit `2`) in CI.

Roll function-level results up by package or workspace area with
`[tool.riskratchet.groups]`. Each function is assigned to the longest
matching repo-relative prefix; ungrouped functions are reported as `null` in
JSON and `ungrouped` in text or markdown.

```toml
[tool.riskratchet.groups]
core = "src/core"
api = ["src/api", "src/public_api"]
```

For `packages/*` / `services/*` layouts where one `coverage.json` is not
practical, declare a per-prefix coverage map (or pass `--coverage-map` on the
CLI; longest matching prefix wins):

```toml
[tool.riskratchet]
paths = ["packages/alpha", "packages/beta"]

[tool.riskratchet.coverage_map]
"packages/alpha" = "packages/alpha/coverage.json"
"packages/beta" = "packages/beta/coverage.json"

[tool.riskratchet.groups]
alpha = "packages/alpha"
beta = "packages/beta"
```

One repo-level baseline (recommended for tight coupling) is global; one
baseline per package is useful when packages release independently. Every
command prints a diagnostic banner to stderr summarizing the resolved root,
scan paths, and coverage source.

## Sample output on real libraries

I ran riskratchet against four widely-used Python libraries to show what its
output looks like on production code. Each was cloned fresh, its own test
suite run with `pytest --cov --cov-report=json:coverage.json`, then scanned.
Top findings:

| Library        | Function                          |       Score |  CC | Line cov        |
| -------------- | --------------------------------- | ----------: | --: | --------------- |
| python-slugify | `__main__::main`                  | 53.1 (high) |   3 | 11% (0% branch) |
| python-slugify | `slugify`                         |        33.3 |  27 | 88%             |
| tabulate       | `_CustomTextWrap._wrap_chunks`    |        44.4 |  31 | 60%             |
| tabulate       | `_normalize_tabular_data`         |        42.6 |  76 | 78%             |
| tabulate       | `tabulate` (entry)                |        37.1 |  62 | 97%             |
| humanize       | `precisedelta`                    |        32.9 |  26 | 100%            |
| humanize       | `naturaldelta`                    |        32.4 |  33 | 100%            |
| inflect        | `engine._sinoun`                  |        36.7 | 108 | 98%             |
| inflect        | `engine._plnoun`                  |        36.2 | 100 | 99%             |

The point is not that these libraries are bad. They have all-green CI and
many users. The point is that even mature, well-tested code accumulates
functions where complexity, coverage, and sprawl combine into something
worth a second pair of eyes. A CC=108 function with 98% coverage is not on
fire; it is a function that works and is tested. The ratchet's job is to
keep those numbers from getting worse over time.

## Comparison with other tools

| Tool        | Per-function risk  | Baseline / ratchet | Combines complexity + coverage + churn |
| ----------- | ------------------ | ------------------ | -------------------------------------- |
| coverage.py | line / branch only | no                 | no                                     |
| radon       | complexity only    | no                 | no                                     |
| xenon       | complexity only    | yes (threshold)    | no                                     |
| pytest-crap | yes (CRAP)         | no                 | partial (CC + line coverage)           |
| riskratchet | yes                | yes                | yes                                    |

## Local development

The same commands run in GitHub Actions:

```bash
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest --cov=src/riskratchet --cov-branch --cov-report=term-missing
uv build --clear
```

Strict typing covers both `src/` and `tests/`.
