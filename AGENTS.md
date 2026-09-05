# AGENTS.md

Operational guide for AI coding agents (Claude Code, Cursor, Copilot, Codex,
Devin, Gemini CLI, Aider) working in this repo or invoking `riskratchet`
from elsewhere.

## What this repo is

`riskratchet` is a Python CLI and pytest plugin that scores per-function
maintainability risk and fails CI when risk grows past a baseline. One
binary, one command, structured output.

## Running the tool

```bash
pip install riskratchet              # or: uvx riskratchet --help
riskratchet --help
riskratchet --version
riskratchet scan src --coverage coverage.json --json
riskratchet baseline src --coverage coverage.json --output .riskratchet.json
riskratchet check src --coverage coverage.json --baseline .riskratchet.json --json
riskratchet diff src --coverage coverage.json --baseline .riskratchet.json --json
```

- Append `--json` to any reporting subcommand for machine-readable stdout.
- Append `--quiet` to `scan` to drop the trailing summary line (pipe-friendly).
- Use `diff` when you need the full baseline comparison: regressions,
  improvements, new functions, removed functions, moved functions, and
  unchanged functions.
- Use `--format github` for GitHub Actions warning annotations and
  `--format pr-comment` for a sticky PR comment body.
- Use `--allow` to suppress known generated/framework functions without
  excluding the whole source file from analysis.
- All error and progress messages go to stderr; stdout is reserved for the
  payload.
- If `--coverage` is omitted (or the file is missing), riskratchet runs the
  configured `[tool.riskratchet] test_command` (default
  `pytest --cov --cov-branch --cov-report=json:{output} -q`) and caches the
  result at `.riskratchet/coverage.json`. The cache is reused while no `.py`
  file under the scan paths is newer. Pass `--no-auto-cov` to opt out (for
  CI pipelines that produce coverage themselves) and
  `--allow-missing-coverage` to tolerate the resulting absence on `baseline`
  and `check`.
- `--churn-days N` (default `90`) sets the lookback window used for the
  `churn` component. Also configurable as `[tool.riskratchet]
  churn_window_days = N`. The CLI value wins over config.
- Config is discovered (since 0.2.7) by walking upward from the current
  directory for the nearest `pyproject.toml` with `[tool.riskratchet]`
  (nearest wins if several ancestors define it); `--config` overrides;
  with no match it falls back silently to the cwd. Path-resolution
  contract: relative config paths (`paths`, `coverage`, `coverage_map`,
  `coverage_cache`, `baseline`) anchor to the config file's directory,
  the auto-coverage test command runs from that directory, and report
  paths are relative to it — so a nested-directory run produces the same
  output as a root run. Explicit `--coverage` / positional paths and the
  no-arg default stay relative to the current directory. Unknown
  `[tool.riskratchet]` keys warn on stderr but do not fail the command,
  and a malformed `pyproject.toml` warns and is skipped during the walk;
  `riskratchet config validate` is the strict (exit 2) gate.
- When `check` exits `1`, a short hint is written to **stderr** with two
  escape hatches: regenerate the baseline (option 1) or loosen the
  per-component regression gate (option 2, only shown when at least one
  regression has `kind == "component_regressed"`). The hint is on stderr
  so `--json` stdout consumers are unaffected.
- `check --fail-above N` (since 0.2.8) is a **no-baseline absolute gate**:
  pass `--fail-above N` and skip `--baseline` to fail when any current
  function's score exceeds `N`. Reports each violating function as a
  `kind: "above_threshold"` regression (`previous_score: null`,
  `delta: null`) in the same envelope as the baseline gate, so JSON
  consumers and SARIF/table/markdown/pr-comment renderers work unchanged.
  `--format pr-comment` in no-baseline mode renders the regressions-only
  PR comment (since 0.2.8 P8); in baseline mode it renders the full
  diff-against-baseline PR comment as before. When both `--baseline`
  (resolved) and `--fail-above` are given, the baseline gate is
  authoritative and `--fail-above` is ignored with a stderr warning —
  for a baseline-aware absolute threshold use `--fail-existing-above`
  instead. Configurable via `[tool.riskratchet] fail_above = N`
  (number in `(0, 100]`).
- **Setup errors are remediation-form** (since 0.2.8). When riskratchet
  cannot start work because of a setup problem — missing coverage,
  missing baseline, malformed baseline, missing scan path, auto-coverage
  produced nothing — it writes a multi-line stderr block in the shape
  `riskratchet: <headline>\n\nFix one of:\n  1. <description>\n       <command>`
  so every first failure suggests the exact command to run next. Tests
  that contract on this shape live in `tests/test_setup_errors.py`;
  rely on the presence of `Fix one of:` and the remediation command
  string, not on the exact headline wording.
- **Zero-flag `scan` prints a next-step footer** (since 0.2.8). When
  `scan` runs without `--quiet`, `--summary`, `--output`, and the
  default `table` format applies, AND no baseline file exists at the
  resolved baseline path, scan appends a stdout footer pointing at
  `riskratchet baseline` (with `riskratchet check --fail-above 60` as
  the no-commitment alternative). The footer adapts to the empty
  state ("0 functions ... nothing to baseline yet"). JSON / SARIF /
  markdown / PR-comment outputs are unaffected because the gate is
  `format == "table"`.

## `is_public` classification

Used by the `public_surface` component and emitted on every function in
`scan --json` / `diff --json`. Determined statically from the AST:

- No `__all__` in module → by qualname. Leading-underscore segments are
  private; dunders (`__init__`, `__call__`, …) are public.
- Module has `__all__` as a static list/tuple of string literals →
  additive promotion. A top-level name in `__all__` is public even with a
  leading underscore. Omission does **not** demote a name that is
  otherwise public by naming rule. Nested segments still follow the
  naming rule, so `_Cls.public_method` is public when `_Cls` is in
  `__all__`, but `_Cls._helper` is not.
- Dynamic `__all__` (augmented assignment, concatenation, multiple
  assignments) falls back to the naming rule.

## Exit codes

| Code | Meaning |
| ---: | --- |
| `0` | success; for `check`, no regressions |
| `1` | for `check`: at least one regression past tolerance |
| `2` | usage error (missing baseline, unknown format, unknown function) |

`scan`, `baseline`, and `explain` never exit with `1`. They exit `0` on
success and `2` on usage errors.

## Output contract and stability

- JSON schemas live in [`schemas/`](schemas/): `report.schema.json`
  (scan), `regressions.schema.json` (check), `diff.schema.json` (diff),
  `baseline.schema.json` (on-disk baseline), `summary.schema.json`
  (`--summary --json` envelope), and `config.schema.json`
  (`config show --json`). Each is exercised against real CLI output in
  `tests/test_schemas.py`.
- Bumping the baseline format is a **two-file** change: `SUPPORTED_BASELINE_VERSIONS`
  in `src/riskratchet/baseline/io.py` and the `version` enum in
  `schemas/baseline.schema.json` must move together. Since 0.3.3 the loader
  *rejects* versions outside its list, so updating only the schema makes
  riskratchet refuse a baseline its own schema calls valid.
  `test_supported_versions_match_the_baseline_schema_enum` is the trip-wire.
- Adding a `[tool.riskratchet]` key is a **four-place** change: `CONFIG_ALLOWED_KEYS`
  (so it is not warned about as unknown), a type rule in `config.invalid_config_values`
  (`_BOOL_KEYS` / `_NUMBER_KEYS` / `_STRING_LIST_KEYS`, so a wrong-typed value is
  rejected rather than silently dropped), `schemas/config.schema.json` together with
  the `config show --json` payload (`_resolved_config_payload`), and the README
  configuration table. Since 0.3.4 the analysis commands and `config validate` share
  those collectors, and `test_analysis_commands_reject_every_config_that_config_validate_rejects`
  is the trip-wire if the two paths ever drift apart again;
  `test_every_allowed_config_key_is_documented_in_the_readme` (0.3.6) is the one for
  the table.
- **An option config can turn on must be turn-off-able by flag.** `typescript = true`
  has `--no-typescript` (and `--riskratchet-no-typescript` in the plugin) because a
  switch only config can flip leaves a one-off run with no way back. A `--x/--no-x`
  Typer pair renders as two sub-columns and squeezes every option's help at 80
  columns, so the house style is a separate `--no-x` option.
- **Python coverage is not applicable when there is nothing to cover.** With
  TypeScript on and no `.py` file under the scan paths (after include/exclude),
  `_resolve_coverage` runs no test command and requires no report; it says so once.
  Before 0.3.6 a TypeScript-only project could not run `scan`, `baseline`, or `check`
  at all — the auto-coverage `pytest` was not on the PATH of a
  `pip install 'riskratchet[typescript]'` environment, and that was exit 2.
- **The Action installs the `[typescript]` extra on every path**, so a repo that turns
  TypeScript on from config never meets the install hint in CI. The three Action
  inputs (`typescript`, `ts-coverage`, `ts-entry`) are passthroughs — empty passes
  nothing and the CLI resolves config itself; the Action never reads `pyproject.toml`.
  `dogfood-action.yml` proves the install and TypeScript scoring on a runner, not just
  the YAML shape.
- A path the user **named** must exist, and the answer cannot depend on which
  fallback happens to succeed. `--coverage` / `--ts-coverage` that do not exist are
  exit 2 (`config._report_missing_coverage`, `config._ensure_ts_coverage_exists`);
  a path from `[tool.riskratchet] coverage` warns and names its substitute, because
  auto-coverage may legitimately fill it. `doctor._check_coverage` keys off the same
  rule via the `coverage_auto` origin — before 0.3.5 it said FAIL where `check` said
  nothing at all, so the two commands disagreed about whether a project was usable.
  Keep them in step: `test_doctor_and_check_agree_about_a_missing_configured_coverage_path`.
- The TypeScript backend mirrors the Python one, and "mirrors" is a claim tests must
  hold to. `missing_coverage` handling is pinned across both backends by
  `test_an_absent_report_scores_the_same_in_both_backends` (parametrized over the whole
  `MissingCoveragePolicy` enum, so a new policy cannot land on one backend alone), and
  every raising JSON loader is pinned for non-object roots *and* structurally wrong
  payloads in `tests/test_coverage.py`. Two docstrings claimed this parity before 0.3.5
  while SKIP scored TypeScript 100% covered and Python 0%.
- Never let an I/O failure exit 1. Exit 1 means a gate tripped; an unwritable
  `--output`, an unreadable source file, or a full disk is exit 2 with a remediation.
  Route writes through `cli._write_or_exit` / `cli._exit_unwritable`.
- **There is more than one door into the gate.** `riskratchet check` is not the only
  entry point: the pytest plugin, `explain`, and `init --with-baseline` all gate or
  write a baseline, and each one used to resolve its own settings. Any new entry point
  routes through `config.discover_config` + `config.resolve_gate_settings` +
  `pipeline.build_report`, never through `engine.analyze` directly — that is what makes
  `[tool.riskratchet]`, redaction, the empty-scan guards, and (since 0.3.6) TypeScript
  apply everywhere. The plugin still called `engine.analyze` until 0.3.6, so a mixed
  baseline was gated on its Python half only.
  `test_the_plugin_and_the_cli_reach_the_same_verdict` and
  `test_the_plugin_gates_typescript_when_config_turns_it_on` are the trip-wires.
- **An option default that is a real value cannot lose to config.** The plugin's
  thresholds defaulted to `50.0` / `5.0` / `15.0`, which is indistinguishable from the
  user passing those numbers, so config could never win. Options default to `None` and
  resolve `flag → config → default` through the `_resolved_*` helpers.
- **One policy, one verdict.** `check` gates via `diff` + `regressions_from_diff`; the
  plugin gates via `compare`. They had silently diverged on the component gate.
  `test_the_two_entry_points_reach_the_same_verdict` sweeps a function across every
  threshold boundary and requires both to agree.
- **A baseline must not lose a language.** `_refuse_to_erase_baseline` asks whether the
  whole report is empty; `_refuse_to_drop_a_language` asks whether a *language* went
  missing. A `baseline` run without `--typescript` over a mixed baseline was silently
  dropping every TS entry while looking non-empty.
  Add to the list rather than replacing it — reading old baselines is what keeps
  an upgrade from forcing every adopter to re-baseline.
- **A report rendered beside an exit code must describe the gate that produced it.**
  `check --format pr-comment` selected rows by `DiffStatus` while the gate keys on
  `RegressionKind` — different sets, each holding members the other cannot, so the
  Action's sticky comment could read "No risk regressions detected" on an exit-1 run
  and show a regression row on an exit-0 run. Both modes now render
  `render_regressions_pr_comment`; a diff attaches as collapsed context, never as the
  verdict. `tests/test_pr_comment_gate_agreement.py` sweeps every `RegressionKind`.
- **Count what you claim to count.** A renderer that says "analyzed" must not print the
  emitted count: `--top`/`--limit` truncate the list, and `_summary_payload` already
  carries `analyzed_functions`, `emitted_functions`, `suppressed_functions`, and
  `skipped_missing_coverage`. Route new renderers through it rather than
  `len(report.functions)`.
- **A snapshot is not a contract.** `tests/__snapshots__/` froze the broken `--format
  github` escaping and the exit-0-with-a-regression-row PR comment for many releases —
  a snapshot pins whatever is emitted, correct or not. Pair every snapshot with an
  assertion about the property that matters, and read every `.ambr` move as a change to
  review, not noise to accept.
- `--format sarif` emits a SARIF 2.1.0 log. The output references
  `https://json.schemastore.org/sarif-2.1.0.json` in its `$schema` field;
  the upstream OASIS definition is the [SARIF 2.1.0 spec](https://docs.oasis-open.org/sarif/sarif/v2.1.0/cs01/sarif-v2.1.0-cs01.html).
  riskratchet does not ship a separate SARIF schema of its own: the normative one is
  vendored at `tests/vendor/sarif-2.1.0.schema.json` and every emitted log is validated
  against it by `tests/test_sarif_schema.py`. Driver name is
  `riskratchet`; rule IDs are `riskratchet.function-risk` (from `scan`) and
  `riskratchet.regression` (from `check`).
- **The two halves of a GitHub workflow command take different escape sets.** The
  runner's `unescapeData` reverses only `%25`/`%0D`/`%0A`; `unescapeProperty` also
  reverses `%3A`/`%2C`. Escaping `:` into a message is therefore one-way, and leaving a
  `file=` value unescaped lets `:` or `,` terminate the property list and drop the
  annotation. `_escape_github_data` and `_escape_github_property` are not
  interchangeable.
- Field names in our native JSON are stable within a minor version (0.x).
  Additive changes (new optional fields) may land in any release; renames
  or removals are called out in `CHANGELOG.md` under a **Breaking** heading.
- Paths in JSON output are repo-relative POSIX paths.
- Risk weights are configurable in `pyproject.toml` under
  `[tool.riskratchet.weights]`. Any subset of the six component keys may be
  overridden; missing keys keep their default, and the whole vector is
  renormalized so the total still maps to `[0, 100]`. Invalid keys or
  negative values exit `2`. See the README for the default values.
- Native JSON output includes `$schema` and `version` fields. `diff --json`
  is validated by `schemas/diff.schema.json`.

## Rename-aware matching (since 0.2.5)

`compare`, `diff`, and `check` recognize renamed/moved functions before
classifying them as new. The matcher uses six signals: body fingerprint,
signature fingerprint (parameters + decorators + return annotation), path
equality, qualname tail (last segment), component-vector cosine
similarity, and score proximity. An unambiguous match becomes
`DiffStatus.MOVED`; a multi-candidate cluster becomes the new
`DiffStatus.AMBIGUOUS_RENAME`, which surfaces in the gating block of the
PR comment and always shows up in `regressions_from_diff` so risk growth
isn't silently masked. New diff JSON fields: `previous_targets` (array),
`match_confidence` (number/null). Existing baselines without the new
optional `signature` field continue to load; new baselines start writing
it on the next `riskratchet baseline` run.

### Rename matcher: known limits

The weights (0.55 body / 0.20 signature / 0.10 path / 0.05 qualname-tail /
0.05 component-vector / 0.05 score) and 0.65 threshold are **provisional**.
They were chosen so body+any-other-signal clears the threshold and
signature-alone+path+tail+score-proximity doesn't. The empirical calibration
harness shipped in 0.2.10, but its corpus work targeted the `sprawl` weights,
not these rename-matcher thresholds — so the thresholds remain provisional
(a corpus of real-world renamed PRs is still future work; see
`docs/riskratchet-0.2x-roadmap.md`). Until then, expect occasional
ambiguity that requires reading the PR diff to resolve.

Signature-only matches are deliberately rejected. A candidate whose body
fingerprint *changed* will not be silently reported as MOVED based on a
matching signature alone — that would let a body rewrite hide behind a
rename. Body fingerprint match + any one other signal is the minimum bar
for an unambiguous match.

## Monorepo / multi-package layouts (since 0.2.5)

When a single coverage.json isn't possible, declare a per-prefix coverage
map:

```toml
[tool.riskratchet]
paths = ["packages/alpha", "packages/beta"]

[tool.riskratchet.coverage_map]
"packages/alpha" = "packages/alpha/coverage.json"
"packages/beta" = "packages/beta/coverage.json"
```

Or pass the same map on the CLI:

```bash
riskratchet scan packages/alpha packages/beta \
  --coverage-map packages/alpha=packages/alpha/coverage.json \
  --coverage-map packages/beta=packages/beta/coverage.json
```

Longest matching prefix wins. The map is mutually exclusive with the
single `--coverage` flag. Every command now prints a diagnostic banner
to stderr summarizing the resolved root, scan paths, and coverage source
(`coverage=single=<path>`, `coverage=map=<prefix:path,...>`, or
`coverage=none`).

Per-package baseline vs repo-level baseline is a documentation choice,
not a code one: run `riskratchet baseline` once per package directory
(each with its own `pyproject.toml` and `.riskratchet.json`) for fully
independent ratchets, or use one repo-level baseline + `[tool.
riskratchet.groups]` for partitioned reporting from a single config.

## CI is the source of truth

GitHub Actions runs the canonical check set. If you are unsure whether a
change is safe, run the same commands the workflow runs (see the README
Local development section) rather than inventing a local approximation.

The scanning commands only *warn* on unknown `[tool.riskratchet]` keys so a
config written for a newer version still runs. Teams that want a typo to fail
instead add `riskratchet config validate` (exit 2 on unknown keys / malformed
config / invalid values) as a one-line strict gate ahead of `riskratchet
check` — the deliberate complement to the warn-by-default behavior.

**Regenerating `.riskratchet.json`: do it in CI, not locally.** Risk scores
depend on environment-sensitive inputs — `churn` uses a wall-clock `git log
--since` window, and a few functions' coverage depends on the filesystem (e.g.
`doctor.py::_find_newer_py` compares file mtimes). A baseline regenerated on a
dev machine (especially macOS) therefore diverges from what the Linux regression
gate recomputes and trips it. Use the **`regenerate-baseline`** workflow
(Actions tab → Run workflow), which regenerates in the gate's own environment
and opens a PR; or, for a surgical add of a new module without disturbing
existing entries, edit only the added/removed entries by hand. Whichever path,
the regression gate (and dogfood) check out with `fetch-depth: 0` so churn sees
full history — a shallow clone silently zeroes it.

## Cutting a release

Releases are cut separately from feature PRs (version bumps never ride a feature
PR — see "Do not"). The release commit lands on `master` and bumps, in lockstep:

- `pyproject.toml` `version` and `uv.lock` (run `uv lock` after the bump);
- the literal pin in `tests/test_release_integrity.py`;
- `ACTION_REF` in `src/riskratchet/init.py` and the `KayhanB21/riskratchet@vX.Y.Z`
  pins in `README.md` (the Action `uses:` block and the pre-commit `rev:`);
- the `## [X.Y.Z]` date in `CHANGELOG.md`.

`tests/test_release_integrity.py` enforces that `ACTION_REF` and the README pins
equal the package version, so a forgotten bump fails CI instead of shipping a stale
ref (the wrapper sat at `v0.2.8` for four releases before this guard existed). Tag
`vX.Y.Z` on `master`; `publish.yml` builds and publishes to PyPI via Trusted
Publishing — there is no manual upload step.

**Cross-repo tail — the Marketplace wrapper.** `KayhanB21/riskratchet-action` is a
*separate* repo whose `action.yml` delegates to `KayhanB21/riskratchet@vX.Y.Z`. After
the PyPI release: bump that `uses:` ref to the new tag, commit to its `master`, tag a
new `v1.0.N`, and **force-move the floating `v1` tag** to it — the Marketplace serves
the `v1` tag, not `master`, so `@v1` consumers stay on the old release until `v1`
moves. The wrapper's `check-delegated-ref` workflow turns CI red within a week if this
is skipped, but do it same-day.

## Developing on this repo

```bash
uv sync
uv run ruff check .
uv run mypy src tests
uv run pytest --cov
uv run riskratchet scan src --coverage coverage.json --json
uv run riskratchet diff src --coverage coverage.json --baseline .riskratchet.json --json
```

Conventions:

- Python 3.10+, strict mypy, ruff format. Line length 110.
- Tests live under `tests/`, mirroring `src/riskratchet/` module names.
- CLI logic stays thin in `src/riskratchet/cli.py`; business logic lives in
  the per-module files (`scoring.py`, `engine.py`, `baseline.py`, etc.).
- Snapshot tests for renderers live in `tests/test_cli_snapshots.py`. If a
  JSON shape changes, update the schema in `schemas/` *and* the snapshot in
  the same commit.

## Do not

- Do not edit files under `dist/`, `.venv/`, or any `__pycache__/`.
- Do not change a JSON field name or remove a field without also updating
  the matching schema in `schemas/` and adding a **Breaking** entry to
  `CHANGELOG.md`.
- Do not bump the package `version` in `pyproject.toml` as part of a feature
  PR. Releases are cut separately.
- Do not add color codes or progress bars to stdout. They break agent
  consumers that parse stdout. Use stderr.

## Where to look first

- CLI entry (commands + dispatch only): `src/riskratchet/cli.py`
- Config discovery / validation / anchoring / value resolution:
  `src/riskratchet/config.py` (since 0.2.7). `cli.py` is a thin shell
  over it — business logic does not live in `cli.py`.
- Scoring: `src/riskratchet/scoring.py`
- Renderers (table / JSON / markdown / SARIF / GitHub annotations):
  `src/riskratchet/reporting/` package (since 0.2.6) — `text.py`,
  `markdown.py`, `json_payload.py`, `sarif.py`, `annotations.py`, and
  shared `summary.py`. External callers import from
  `riskratchet.reporting`; the submodule layout is internal.
- Baseline I/O and comparison: `src/riskratchet/baseline/` package
  (since 0.2.7) — `io.py` (JSON load/save), `compare.py` (the `check`
  gate), `diff.py` (full comparison), `regressions.py` (diff → failing
  regressions), and shared `classify.py` (matching ladder +
  component-regression policy). External callers import from
  `riskratchet.baseline`; the submodule layout is internal. The rename
  matcher is `src/riskratchet/matching.py` (top-level; also used by
  `analysis`, so it intentionally does not live inside `baseline/`).
- Pytest plugin: `src/riskratchet/pytest_plugin.py`
- Schemas: `schemas/`
- Snapshot tests use `syrupy` (since 0.2.6). To regenerate after an
  intentional output change: `uv run pytest --snapshot-update`.
  Snapshots live in `tests/__snapshots__/`. Shared in-memory
  fixtures are in `tests/reporting_fixtures.py`.
- Reporting layering rule (since 0.2.6): family submodules under
  `src/riskratchet/reporting/` (`text`, `markdown`, `json_payload`,
  `sarif`, `annotations`) may only import from `summary` (the leaf),
  never from each other. Enforced by `tests/test_reporting_layering.py`.
- Baseline layering rule (since 0.2.7): family submodules under
  `src/riskratchet/baseline/` (`compare`, `diff`, `regressions`) may only
  import from the leaves (`io`, `classify`), never from each other.
  Enforced by `tests/test_baseline_layering.py`.
