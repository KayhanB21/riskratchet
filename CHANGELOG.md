# Changelog

All notable changes to `riskratchet` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

JSON-output stability policy (see [`AGENTS.md`](AGENTS.md)): field names
in `scan --json`, `check --json`, and the baseline file are stable within
a minor version. Additive changes (new optional fields) may land in any
release; renames or removals are called out below under **Breaking**.

## [0.3.6] - 2026-09-04

TypeScript through every door. The backend has scored TypeScript since `0.2.12`, and
`0.3.5` warned when a mixed baseline was being gated on its Python half — but the
officially shipped CI path could not obey that warning: `typescript` was not a config
key, the Action had no way to pass `--typescript`, and its install step never installed
the `[typescript]` extra. This release makes the switch a config key, teaches every
entrance the last three releases hardened — the Action, the pytest plugin, `init`,
`explain`, `doctor` — to honour it, closes the TypeScript silences that surfaced on the
way, and makes `check` say what it did not check. The first **Added** heading since
`0.3.0`.

### Added

- **`typescript`, `ts_coverage`, `ts_entry` are `[tool.riskratchet]` keys.** Validated
  (`typescript = "yes"` is exit `2`), shown by `config show`, in `config.schema.json`,
  and in the README table — a trip-wire test now requires every allowed key to be
  documented there. The two lists anchor to the config directory like `coverage`; CLI
  values stay cwd-relative. **`--no-typescript`** is the explicit off that beats
  `typescript = true` and the deprecated `--experimental-typescript` alias, on every
  command: an option config can turn on must be turn-off-able by flag.
- **Action inputs `typescript`, `ts-coverage`, `ts-entry`.** Passthroughs: `true` →
  `--typescript`, `false` → `--no-typescript`, empty → nothing, so the CLI resolves
  config; anything else fails the step before the CLI runs. The two path inputs are
  space-separated like `paths`, one repeated flag per entry, workspace-relative. The
  Action now installs `riskratchet[typescript]` on every install path (PyPI latest, a
  pinned version, a local wheel), so a config-driven repo never sees the install hint in
  CI. Config-driven TypeScript therefore needs the Action at **`v0.3.6` or later**: an
  older `action.yml` with an unpinned version installs the 0.3.6 CLI without the extra
  and exits `2` with the hint.
- **Pytest plugin options `--riskratchet-typescript`, `--riskratchet-no-typescript`,
  `--riskratchet-ts-coverage`, `--riskratchet-ts-entry`.** Both switches off means config
  decides.
- **`explain --ts-coverage` / `--ts-entry`**, for symmetry with the keys.
- **`init` recognises a TypeScript tree.** When `src` holds `.ts` files the starter block
  gains `typescript = true` and a commented `ts_coverage` hint, and the next steps become
  `pip install 'riskratchet[typescript]'` → the runner's coverage command → `baseline` /
  `check` naming only the reports the tree needs. `init --with-baseline` builds the first
  baseline with the resolved TypeScript settings. Python-only output is byte-identical.
- **`doctor` check `ts-coverage`** (the `doctor --json` check-name enum is extended),
  only when TypeScript is on: nothing configured → WARN "TypeScript functions score as
  uncovered" with the vitest + `ts_coverage` remediation; configured but missing or
  malformed → FAIL; older than a `.ts` under the paths, or zero overlap with the scanned
  `.ts` set → WARN; else PASS.
- **`check` says how much of the baseline it compared.** Every format carries
  `Baseline: N entries · M compared · K not seen this run`; `--summary` prints the same
  counts; `check --json` gains an additive `baseline` object (`entries`, `compared`,
  `removed`; absent in `--fail-above` mode).
- **The unscanned-files warning.** When baseline entries live in files that still exist
  under the scanned paths but were not scanned this run — an `include` / `exclude`
  hiding them, not a deletion and not a deliberate subset such as `check packages/api` —
  `check`, `diff`, and the pytest plugin warn with counts only (safe under
  `private_comment`), and never fail. The `0.3.4` carry-over: three of four baseline
  entries hidden by an `exclude` printed "No risk regressions detected." and exited `0`;
  only the PR comment's collapsed diff said `Removed: 3`.
- **The rename + edit note.** When the diff holds both new and removed entries, `check`
  prints one stderr note naming the rule: a renamed function whose body also changed is
  gated as new against `fail_new_above`, not as a regression against its old score, and
  `riskratchet diff` lists both sets. The README gains the "Rename matcher: known limits"
  paragraph that lived only in `AGENTS.md`.
- **`skipped_generated_files`** in every report summary (`scan --json`, the text and
  markdown headers, the summary line): the files a `@generated` header removed from
  scoring, in both languages (see **Changed**).
- `check --json` regression objects carry `group` (the scan and diff payloads already
  did); `explain --summary --json` carries `language` (the one identity field the summary
  lost). A scan path outside the config directory is named once on stderr.

### Changed

- **A TypeScript-only project runs.** On `0.3.5`, `scan`, `baseline`, and
  `check --typescript` all exited `2` on a tree with no `.py` files: auto-coverage tried
  to run `pytest` to cover nothing, and `--allow-missing-coverage` never reached the
  unstartable-runner branch. When TypeScript is on and nothing under the scan paths is
  Python, Python coverage is *not applicable* — no auto-coverage, no exit `2`, one stderr
  line — and `doctor` reports the coverage row the same way, skipping the overlap and
  branch-data rows. `summary.total` in `doctor --json` therefore moves for
  TypeScript-only projects.
- **`allow_missing_coverage` downgrades an unstartable test command** (`pytest` off the
  PATH) to a warning. The flag promises "continue when coverage is absent", and a runner
  that cannot start yields absent coverage.
- **Missing TypeScript reports follow the `required` split `coverage` already had.** A
  report named on the CLI (`--ts-coverage`) is exit `2` everywhere, unchanged. A report
  named in config (`ts_coverage`) warns on `scan` / `explain` and is exit `2` on
  `baseline` / `check` / `diff`, downgraded by `allow_missing_coverage` — which on
  `0.3.5` returned early and let the strict loader exit `2` anyway, so the remediation it
  printed was not one.
- **`explain --typescript` without the `[typescript]` extra is exit `2`** with the
  install hint; it was exit `1` plus a traceback. `explain` and `init --with-baseline` now
  build through the same boundary as every other command.
- **`doctor` FAILs when config enables TypeScript and the extra is absent**, with the
  install command; it tries the import, not just the dist metadata, because `check`
  exits `2` on exactly that import. When only the baseline records a TypeScript identity
  the row stays a WARN. With `typescript = true` a scan path holding `.ts` files is PASS
  instead of `WARN no .py files`; with it off, `.ts` files under the paths WARN that they
  are not scored, naming the key.
- **A `# @generated` Python file leaves the scored set.** The TypeScript backend has
  honoured a comment-anchored `@generated` header since `0.2.12` and its docstring
  claimed the Python backend mirrored it; it did not, so such a file was scored in full.
  Both backends now skip the file's functions, count it in `skipped_generated_files`, and
  list it in `files[]` with zero functions. The `*.pb.ts` / `*.gen.ts` name rule is
  deliberately not mirrored for Python (codegen headers say `DO NOT EDIT` in a hundred
  spellings); the README recipe is `exclude = ["**/*_pb2.py"]`. Entries the header
  removes appear as removed in `diff`, never as regressions.
- **`files[]` lists every file the scan reached.** A Python file with a syntax error or a
  `@generated` header is listed with zero functions, as the TypeScript backend already
  did, so `total_files` moves for repos that have them — a skipped population shows up in
  the count rather than vanishing.
- **One key per file.** A file outside the config directory is keyed by one `../…`
  spelling whatever cwd or spelling it was passed with; `0.3.5` kept whatever spelling
  the caller used, so the identity changed with the invocation. A symlinked scan root
  keeps its `src/x.py` key. SARIF `artifactLocation.uri` is that key verbatim, so it
  equals `properties.path` always — it used to re-derive an absolute path against the
  process cwd. A baseline written from an absolute out-of-root spelling carries across
  through the fingerprint matcher; `riskratchet baseline` settles the keys.
- **The pytest plugin gates through `diff` + `regressions_from_diff`**, as `check` does,
  instead of `compare`. Same verdict (the `0.3.5` property test still pins it); the diff
  is what knows what was *not* seen, which is how the plugin prints the unscanned-files
  warning.
- The "baseline holds N typescript entries but this run analyzed no typescript" hint
  names both ways to enable the backend; the Python-side "analyzed no python" warning
  names `include` / `exclude` as the likely cause.

No exit-code change above can turn a green gate red: each turns an exit `2` into `0` or
a warning for a project that could not run, or reports a setup failure that was already
failing. Two score changes can, and only for a TypeScript project whose `ts_entry` is
`@generated` or does not parse (see **Fixed**): its functions regain their real
`public_surface`, the first `check` after upgrade says so, and `riskratchet baseline`
accepts the corrected scores.

### Fixed

- **`config.schema.json` said `missing_coverage ∈ {pessimistic, skip, zero}`**; the
  enum is `optimistic`, so `config show --json` failed its own schema for one legal
  value. A per-policy test now sweeps the enum.
- **A `@generated` TypeScript file was dropped with no warning and no counter**, and a
  `@generated` *entry* barrel demoted every TypeScript function to internal
  (`public_surface` 100 → 0) behind the ordinary "public surface narrowed" line. A
  generated file is valid TypeScript: it is now parsed for its real exports and only its
  functions are skipped, so `export * from './generated'` keeps resolving and a generated
  entry narrows correctly — guarded by a test that a good barrel re-exporting a generated
  module narrows exactly as on `0.3.5`.
- **An entry file that did not parse narrowed the public surface to nothing.** A
  syntax-broken or unreadable `ts_entry` demoted every function to internal the same
  way. Narrowing now refuses on an unproven graph: `check` warns
  `entry index.ts did not parse; the surface can't be bounded — keeping file-level export
  flags` and leaves the flags untouched. A file that is only re-exported keeps the
  empty-module behaviour.
- **TypeScript discovery descended into `node_modules`**; only hidden directories were
  skipped. A `node_modules` named explicitly as the scan path is still honoured.
- **`doctor` on a TypeScript-only package** warned `no .py files` with a remediation
  about package roots, prescribed pytest for coverage, never checked TypeScript coverage,
  and keyed its `typescript` row off the baseline identity alone (see **Added** and
  **Changed**). `doctor` and `check` now agree on whether such a project can run.
- **The pytest plugin had no TypeScript path.** It called `engine.analyze` directly, so
  a mixed baseline under `typescript = true` would have been gated on its Python half
  only, against the invariant `AGENTS.md` states. It now builds through
  `pipeline.build_report` with the CLI's pieces it never had: the missing-report rule,
  the exception boundary (`ImportError` / `FileNotFoundError` / `ValueError` fail the
  session with the message, never a traceback), the grammar-bump identity guard with the
  exact re-baseline command, and the "baseline holds N typescript entries" warning.
- **The regression hint printed the raw baseline path under `--private-comment`**
  beside a hashed stdout; it now prints `<baseline.json>`.

### Internal

- `config.resolve_gate_settings` / `GateSettings` carry `typescript`, `ts_coverage`,
  `ts_entry`, and `allow_missing_coverage`; `resolved_typescript`, `resolved_ts_paths`,
  `usable_ts_coverage`, and `missing_ts_report_message` are the public halves every entry
  point resolves through. `languages_not_scanned` and `unscanned_baseline_files` live in
  `baseline.classify` (a leaf) so the CLI and the plugin render the same facts;
  `DiffReport.baseline_entries` feeds the baseline line.
- `dogfood-action.yml` gains a second job that stages a TypeScript-only project and gates
  it through `action.yml` in baseline mode — the mode in which a missing `--typescript`
  or a missing extra is exit `2` rather than a silent pass — proving the install and
  TypeScript scoring on a runner. The Action's check step is executed under bash against
  a stub `riskratchet` in the test suite, so input handling is tested as behaviour, not
  grepped.
- Adding a config key is a four-place change (allowed keys, type rule, schema +
  `config show` payload, README table), each with a trip-wire. `AGENTS.md` records the
  invariants this release added: an option config can turn on must be turn-off-able by
  flag; Python coverage is not applicable when there is nothing to cover; the Action
  installs the extra on every path; a skipped file is still a file the scan reached;
  narrowing refuses on an unproven graph; the gate must say what it did not check; one
  key per file.
- Tests 1081 → 1176; coverage 92.16% → 92.02%.

## [0.3.5] - 2026-08-28

The gate has more than one door. `0.3.3` and `0.3.4` hardened the main `check` path;
this release closes the same class of failure at the other entrances — the coverage
inputs, the pytest plugin, `explain`, `init --with-baseline`, the TypeScript backend,
and the report emitters. As before, in every case the correct handling already existed
somewhere in riskratchet; one path simply never routed into it.

### Fixed

- **A missing coverage file silently became a different one.** A typo'd `--coverage`
  fell through to auto-coverage and gated against a file riskratchet generated itself,
  turning a real exit-`1` regression into `0` — while `doctor` reported the same setup
  as FAIL. `baseline` had the destructive twin, anchoring the ratchet to coverage
  nobody asked for. A CLI path now exits `2` before the test command runs; a
  config-supplied path warns and names the substitute actually used;
  `--allow-missing-coverage` downgrades both. `doctor` and `check` now apply one rule.
- **The TypeScript backend treated an unreadable coverage report as a readable one.**
  `has_coverage` was derived from the *requested* paths, not the *loaded* data, so a
  missing or corrupt `--ts-coverage` under `missing_coverage = "skip"` dropped every
  TypeScript function and exited `0`. Separately, `skip` with no report at all scored
  TypeScript 100% covered where Python scores 0% — 40% of the score weight. Both
  backends now score an absent report identically under every policy, enforced by a
  parity test.
- **`[tool.riskratchet] include` was inert in every command** — `include or []` while
  the adjacent `exclude` and `allow` lines correctly read config. It was validated,
  schema'd, documented, honoured by `doctor`, and recommended by the empty-scan
  remediation; `doctor`'s coverage-overlap check therefore evaluated a narrower file
  set than the run it was diagnosing.
- **The pytest plugin skipped every check the CLI runs.** It read no
  `[tool.riskratchet]` at all: hardcoded `src`, its own thresholds, no weights, no
  redaction (raw paths in CI logs under `private_comment = true`), no zero-function
  guard, no shallow-clone warning. It now resolves through the same
  `resolve_gate_settings` the CLI uses; a CLI↔plugin parity test pins verdict and
  rendering. Its threshold options default to unset so config can win.
- **`check` and the plugin implemented two different gates.** The diff path returned
  `IMPROVED` before running the component check, so `check` skipped the component gate
  exactly when the total moved the other way — the one case it exists for. A property
  test now requires `compare` and `diff`+`regressions_from_diff` to agree.
- **`explain` computed identity against the process cwd**, so from a nested directory
  it rejected the very `path::qualname` that `check` had just printed. It now routes
  through the shared report builder and gains `--coverage-map`, `--missing-coverage`,
  and `--typescript`. `init --with-baseline` likewise ignored an existing config and
  skipped the erase guard; both now use the same path as every other command.
- **An `allow` pattern in canonical `path::qualname` form suppressed nothing** — the
  one spelling riskratchet itself emits was the one that could not match. Patterns now
  dispatch on `::`/path-like/qualname through one matcher shared by both backends, and
  configured patterns that suppress nothing warn.
- **`riskratchet baseline` silently erased TypeScript entries.** A run without
  `--typescript` over a mixed baseline dropped every TS entry while the Python half
  kept the report non-empty, so the erase guard never fired. The unit that must not
  vanish is a language, not the file; `check`/`diff` warn on the read side.
- **A baseline whose entries all fail to parse degraded to zero entries** — which
  passes every gate, and then fed `0` into the empty-scan guard, one condition
  disabling two guards. Now exit `2`; `doctor` reports dropped entries and WARNs on a
  zero-entry baseline.
- **The Action's sticky PR comment could contradict the exit code beside it.** In
  baseline mode `check --format pr-comment` selected rows by diff status while the
  gate keys on regression kind: an `existing_above_threshold` failure (exit `1`)
  rendered "No risk regressions detected", and a new function below threshold
  (exit `0`) rendered as a visible regression row. Both modes now render the set the
  gate acted on; the diff attaches underneath as collapsed context with its own
  `Since the baseline:` label. A test sweeps every regression kind and asserts body
  and exit code agree.
- **`--format github` had its escape sets inverted.** `:` was escaped in the message —
  which the runner never unescapes, so every annotation ever written rendered
  `src/m.py%3A%3Ahuge` — while the `file=` property was not escaped at all, so a path
  containing `:` or `,` broke parsing and dropped the annotation.
- **`--format markdown` printed the emitted count under "Functions analyzed"**
  (`--top 5` read "5" on a 400-function repo) and was the only renderer that never
  disclosed suppressed or skipped functions. The missing-coverage disclosure also
  reached only the table renderer; it now lives in the summary line every format
  shares, so the PR comment can distinguish "no data" from "no tests".

### Changed

- **Exit `2` for a coverage path named on the CLI that does not exist** (`--coverage`,
  `--ts-coverage`), reported before the test command runs. A config-supplied path
  warns instead; `--allow-missing-coverage` downgrades both.
- **Exit `2` for a report riskratchet cannot write** — `--output`,
  `--debug-json-file`, or `baseline --output` pointing at a directory or read-only
  location was exit `1` plus a traceback. Exit `1` means a gate tripped; a full disk
  is not a gate. An unreadable `.ts` file likewise warns and continues like the
  Python backend instead of crashing.
- **Exit `2` for a test command auto-coverage cannot start.** `pytest` missing from
  PATH surfaced as an uncaught `FileNotFoundError` and exit `1`, indistinguishable
  from a risk regression. An unparseable or empty `test_command` reports the same
  way. A command that runs and *fails* is unchanged — coverage it wrote is still used.
- **Exit `2` for a baseline whose entries all fail to parse**, and for
  `riskratchet baseline` when writing would drop every entry of a language.
- The pytest plugin's `--riskratchet-*` threshold options now default to unset and
  resolve flag → config → default, so `[tool.riskratchet]` values apply. Passing a
  flag explicitly still wins.

Every exit-code change above can only turn CI red for a repo whose gate was already
not doing what its config said.

### Internal

- `config.resolve_gate_settings` + `GateSettings`: one façade any entry point can use
  to resolve the full gate configuration; the plugin is its first consumer.
  `resolve_redaction` moved from `cli` to `config` so non-CLI entry points can reach
  it. `pattern_matches` is shared by both engines.
- `--format sarif` output is validated against the vendored normative SARIF 2.1.0
  schema in the test suite (`tests/vendor/`), including the empty-`results` case —
  previously only a `$schema` string assertion and a snapshot guarded it, which is how
  the github-format escaping drifted undetected. Two snapshots that had frozen buggy
  output (the `%3A%3A` annotations, an exit-0 comment showing a finding) were
  corrected deliberately rather than regenerated blind.
- Tests 990 → 1081; coverage 91.62% → 92.16%.

## [0.3.4] - 2026-08-22

A gate that checks nothing must say so. `0.3.3` stopped a corrupt *baseline file* from
silently disengaging the ratchet; this release closes the same failure one layer out, in the
config and the scan inputs. In every case below the correct handling already existed
somewhere in riskratchet — one path simply never routed into it.

### Fixed

- **A scan that found nothing passed the gate.** `check` reported "No risk regressions
  detected" and exited `0` whenever the scan produced zero functions, however that happened.
  A *nonexistent* scan path was already exit `2`; an existing path matching nothing was not,
  so a typo'd `paths`, a `src/`→`lib/` restructure, an over-broad `exclude`, or `allow`
  patterns that swallowed everything switched the ratchet off and passed green forever. A
  regression that exits `1` could be hidden to `0` by adding `--exclude 'src/**'`. `diff`
  already reported those entries as `removed`; `check` never acted on it.
- **A known config key with an unusable value was silently dropped.** `fail_new_above = 1`
  applied the gate; `fail_new_above = "1"` exited `0` — the value was discarded and the
  *default* `50` used instead, so a repo that thought it had tightened its gate had not.
  Confirmed across all nine gate-affecting keys: `config validate` caught 9/9 with exact
  messages, the analysis commands warned on 0/9. Note the asymmetry that made it easy to
  miss: a *misspelled key* warned, a correctly-spelled key with a bad value did not.
- **`riskratchet baseline` could erase the ratchet.** The same misconfiguration that made
  `check` pass silently made `baseline` overwrite a populated baseline with a zero-function
  one, discarding every entry. It now refuses and leaves the file byte-identical. Writing a
  *new* zero-function baseline still works — only erasing entries is refused.
- **A non-object coverage file crashed instead of erroring.** `coverage.load_coverage` went
  straight to `raw.get("files")`, so a top-level JSON array reached the user as a raw
  `AttributeError` traceback and exit `1`. It was the only one of the four JSON loaders
  missing the guard — while `typescript_coverage.load_istanbul_coverage`'s docstring claimed
  to *mirror* it. Now exit `2` with a remediation, like every other unreadable input.

### Changed

- **Exit `2` on a `[tool.riskratchet]` value this build cannot use**, from `scan`, `check`,
  `diff`, `baseline`, and `explain`. Every unusable value is reported in one run. This
  restores consistency rather than adding a policy: `weights`, `groups`, `missing_coverage`,
  and `churn_window_days` already exited `2` in those same commands.
  **Unknown keys still only warn** — one may come from a config written for a newer
  riskratchet, and refusing to run would make upgrading riskratchet the only way to downgrade
  it. A known key with a wrong-typed value has no such forward-compatibility story.
  `doctor` is the one exception and keeps reporting without exiting: bailing out before the
  table rendered would make the command whose job is explaining a broken setup the one command
  that refuses to look at it.
- **Exit `2` when `check` scans zero functions and the baseline has entries.** Scoped to that
  combination, which no working config produces — a legitimate subset run
  (`check src/onepackage`) still yields functions and is untouched, and zero functions against
  an *empty* baseline is a legitimately empty project that only warns. The message names which
  of the two causes applies (nothing discovered vs everything suppressed) because the fixes
  differ. `scan` and `diff` warn without changing their exit code.

Both exit-code changes can only turn CI red for a repo whose gate was already not doing what
its config said.

### Internal

- `config._validate_config` is split into two pure, total collectors (`unknown_config_keys`,
  `invalid_config_values`) shared by the analysis commands, `config validate`, and `doctor`,
  with `_validate_config` kept as a shim so its exit codes and messages are unchanged. A
  parity test asserts everything `config validate` rejects, the analysis commands reject.
- `doctor`'s `config` row no longer returns early on an unknown key, which used to hide every
  type error behind a single typo.
- A loader-parity test pins all three raising JSON loaders together, so
  `load_istanbul_coverage`'s "mirrors `coverage.load_coverage`" docstring stays true.
- Test suite 920 → 990; coverage 91.12% → 91.62% against `fail_under = 85`.

## [0.3.3] - 2026-08-15

### Fixed

- **A corrupt baseline silently switched the ratchet off.** `load_baseline` trusted
  `.riskratchet.json` completely: a file that was not a JSON object, had no `entries` array, or
  declared a `version` this build cannot read all degraded to *zero entries* — and an empty baseline
  passes every gate. `check` and `diff` reported "No risk regressions detected" and exited `0` on a
  baseline that ratcheted nothing. All of these are now exit `2` with a remediation command. A
  baseline written by a *newer* riskratchet says so specifically and tells you to upgrade, since
  regenerating would be the wrong fix. Reading v1/v2/v3 baselines is unchanged, so upgrading still
  never forces a re-baseline.
- **A non-dict baseline crashed with a raw traceback.** `[1,2,3]` as a baseline reached the user as
  an unhandled `AttributeError` and exit `1`; it is now a normal setup error.
- **Individually malformed baseline entries vanished without a word.** They were skipped silently,
  quietly leaving those functions unratcheted. The run still continues, but now warns with the
  count. The pytest plugin, which had no error boundary at all around the baseline, now fails the
  session with a message instead of raising.

### Changed

- A structurally invalid baseline is now a hard error (exit `2`) where it previously exited `0`.
  This can only turn CI red for a repository whose ratchet was already not working. No CLI flag,
  JSON field, schema field, or import was removed or renamed.

### Internal

- **riskratchet was mismeasuring its own coverage by 7.6 points.** The `pytest11` entry point makes
  pytest import `riskratchet.pytest_plugin` — and through it `riskratchet/__init__.py`'s eager
  re-exports — during plugin registration, before pytest-cov starts its tracer. Every module-level
  line in `src/riskratchet/**` had therefore already executed by the time measurement began and read
  back as "missing". Adding `-p no:riskratchet` to `addopts` takes the measured total from 83.3% to
  91.1% and fixes all six coverage producers at once, since each inherits it. `fail_under` rises
  74 → 85 to match, and the baseline was regenerated: 120 of 400 entries move, **every one of them
  downward**, because better measurement can only add executed lines. **No adopter impact** — an
  adopter's `[tool.coverage.run] source` never includes riskratchet's own modules, so this only ever
  affected riskratchet measuring itself.
- `pytest_addoption` gained direct unit tests pinning the whole option table (names, defaults, types,
  help text). It had never been measured under either the old or the new setup; it drops 43.0 → 3.0.
- `docs/top-risk.{md,json}` refreshed. They had been stale since May and were publishing this very
  bug — ranking `pytest_addoption` at 43.0 with "0% LCov", alongside functions since moved or deleted.

## [0.3.2] - 2026-08-08

A non-breaking stabilization patch on the `0.3.x` line. Theme: **the adopter's first hour** — every
fix below is on the path a new user actually walks. No CLI flag, JSON field, schema field, or import
was removed or renamed.

### Fixed

- **The GitHub Action could fail on first adoption.** With no baseline yet, the Action runs
  `check --fail-above --format pr-comment`, which emitted *every* function above the threshold with no
  cap — riskratchet's own repo produced 345 rows / ~49k characters. Past GitHub's 65,536-character
  comment limit the upsert step got a 422 and failed the job instead of posting a report. The
  regressions comment now shows 20 rows and collapses the rest into `<details>` (matching the report
  and diff comments), and all three PR-comment renderers are hard-capped below the limit so
  `--limit 0` can't reintroduce the failure. `action.yml` also now truncates rather than appends its
  comment file, closing the same failure from the other end.
- **The documented CI snippet silently zeroed churn.** The README snippet and `riskratchet init`
  both used a bare `actions/checkout`, which defaults to a depth-1 clone. `git log --since` then sees
  only HEAD, so every function's churn component scored as zero and CI disagreed with a
  locally-generated baseline — with no error anywhere. Both now request `fetch-depth: 0`, a test pins
  the README and `init` snippets together so they can't drift again, and a shallow clone is now
  reported at runtime and by `doctor`.
- **An unreadable coverage file crashed with a traceback.** `scan`/`check`/`diff`/`baseline` wrapped
  report-building in `except ImportError` only, so a malformed, truncated, or unreadable coverage
  report escaped as a raw `ValueError`. All four now share one error boundary and exit 2 with a
  copy-pasteable remediation.
- **`--coverage-map` crashed on a missing shard despite promising not to.** riskratchet printed
  "treating as no coverage" and then raised `FileNotFoundError` anyway, so every `scan --coverage-map`
  run with one absent shard died. Unusable shards (missing *or* malformed) are now skipped with one
  warning, matching how the TypeScript coverage loader already behaved.
- **`--help` was visibly broken.** Typer renders help through Rich, which parses `[...]` as a style
  tag and dropped it: `[tool.riskratchet]` vanished ("Falls back to  paths if omitted.") and the
  TypeScript hint rendered as `pip install 'riskratchet'` — a wrong, copy-pasteable command. Both now
  render correctly. Separately, ~12 options each on `check`/`diff`/`baseline` shipped with no help
  text at all; every option is now documented, `--format` and friends list their valid values, and a
  test fails the next undocumented flag.

### Added

- **`doctor` gained checks**, all additive and all WARN except one. New: shallow-clone detection,
  coverage/scan-path overlap ("0 of 214 scanned files appear in coverage" — the most common real
  misconfiguration), missing branch data (which zeroes `branch_gap`, 15% of the weight), and a
  TypeScript grammar-vs-baseline identity check. `doctor` now also parses the coverage file rather
  than only stat-ing it — the one new **FAIL**, and only where `check` was already crashing. It also
  no longer reports "no coverage configured" for projects using `coverage_map` or `auto_coverage`,
  and validates config *values*, not just key names. The `checks[]` `name` enum in
  `schemas/doctor.schema.json` gained the new names; a healthy project's exit code is unchanged.
- **Documentation**: a full `[tool.riskratchet]` configuration reference (13 keys, including the
  entire `fail_*` family, had zero README coverage), an exit-code table, an explanation of the
  component-regression gate (on by default and previously undocumented), and a "Baseline format"
  section covering v3 and the `identity` block — which the README already linked to but never had.

## [0.3.1] - 2026-08-01

A non-breaking stabilization patch on the `0.3.x` line.

### Added

- **Assisted re-baseline on a TypeScript grammar bump.** When `check`/`diff` run `--typescript` against a
  baseline whose recorded `tree-sitter-typescript` grammar or fingerprint scheme differs from the runtime
  (the normal consequence of upgrading the `[typescript]` extra), riskratchet already falls back to
  id-only matching for TypeScript entries and warns "re-baseline recommended." It now also prints the exact,
  copy-pasteable regeneration command — `riskratchet baseline <paths> --typescript [--ts-coverage …]
  --output <baseline_file>` — reusing the current run's paths and coverage reports, so adopters no longer
  reconstruct it by hand. Stderr-only; `--json` stdout is unaffected. No flag, field, or schema change.

## [0.3.0] - 2026-07-25

The first breaking minor — "final calibration before 1.0." Two independent fronts land together: the
**sprawl scoring recalibration** (Python weights corrected on three converging lines of evidence) and
the **TypeScript-scoring promotion** (TypeScript goes from six informational slices to a first-class
scored backend). Both regenerate the dogfood baseline. See the per-front **Breaking** entries below.

### Breaking

- **The `sprawl` component no longer counts file length.** `sprawl` is now the function-length
  term only; the file-line half was **dropped**. Previously `sprawl = (function_length_term +
  file_length_term) / 2`, which meant every function in a large file inherited a size penalty it
  did not earn — a short helper in a 1,200-line module scored the same sprawl as a genuinely
  sprawling function, and cosmetically splitting a file lowered scores with no real maintainability
  gain. Three independent labelled-outcome analyses agree the file-line half carries **no
  predictive signal** (the 0.2.10 SZZ defect ablation, the phase-4 change-proneness ablation, and a
  new 0.3.0 change-proneness gradient that added a messy/AI-side-project cohort and still found it
  net-noise, with no god-module regime reasserting). See
  [`docs/sprawl-component-finding.md`](docs/sprawl-component-finding.md) "Decision for 0.3.0".
  - **Effect on scores:** functions in large files score **lower** (the confound is gone); a small
    minority of genuinely long functions score **higher** (they are no longer averaged-down by
    their file). Component weights are unchanged — only the `sprawl` definition changed.
  - **Baselines must be regenerated.** Because per-function `score` and the `sprawl` component
    change, existing `.riskratchet.json` baselines no longer match; regenerate with
    `riskratchet baseline <paths> --coverage <coverage.json>` and record a bump rationale.
  - `sprawl` is now a pure per-function signal (a function-length count), independent of the file —
    which also makes it **language-neutral** for the TypeScript backend entering scoring in 0.3.0.

- **TypeScript is now a scored backend (`--typescript` on `scan`, `check`, `diff`).** After six
  informational slices (0.2.11–0.2.16), TypeScript is promoted from discovery to first-class scoring.
  `--typescript` analyzes and **scores** TypeScript functions, mixing them into the shared
  `functions[]` array with `language: "typescript"`, using a corpus-derived TS complexity calibration
  (equal complexity percentiles map to equal normalized scores across languages) and optional
  `--ts-coverage` (Istanbul/nyc/c8/Jest/Vitest/Karma; LCOV or Istanbul JSON, validated against real
  output). `check`/`diff` gate and diff scored TS functions through the same language-neutral ratchet
  as Python.
  - **Output break:** the separate informational top-level `typescript[]` array (JSON) and the
    `riskratchet.typescript-function` SARIF note rule are **removed** — TypeScript functions are now
    ordinary scored entries in `functions[]` / regular `riskratchet.function-risk` SARIF results,
    tagged `language: "typescript"`. `functions[].language` in the report/explain schemas relaxes
    from `const "python"` to `enum ["python", "typescript"]`.
  - **Flag rename:** `--experimental-typescript` is deprecated (hidden alias for `--typescript`, one
    release) and now **scores** rather than merely listing; the experimental stderr banner and
    listing are gone.
  - The Python-only path is byte-for-byte unchanged — `engine.py` never imports tree-sitter, and a
    scan without `--typescript` imports neither the TypeScript backend nor tree-sitter. The
    `[typescript]` extra (`pip install 'riskratchet[typescript]'`) is still required to use it.

- **Baseline format v3.** `BASELINE_VERSION` bumps `"2"` → `"3"`. Baseline entries gain an optional
  `language` field (**omitted for Python entries**, so a Python-only baseline changes only the
  top-level version string — no entry churn), and a baseline that contains TypeScript entries gains a
  top-level `identity` block recording the fingerprint scheme + pinned tree-sitter-typescript grammar
  those fingerprints were made with. On `check`/`diff`, if the baseline's recorded TS grammar/scheme
  differs from the runtime's (a grammar bump silently changes every fingerprint), TypeScript functions
  are matched **by id only** and the tool warns "re-baseline recommended" — a bump is detected, not
  read as a mass rename. **v2 baselines load unchanged** (every entry reads as `language: "python"`,
  no identity), so existing Python baselines keep working; regenerate to adopt v3.
  - **Fingerprint scheme 2.** A completeness audit before this first fingerprint-persisting release
    closed two anonymous-token collisions in the TypeScript identity serializer (`const` vs `let`,
    and a `readonly` parameter property), bumping `SCHEME_VERSION` `1` → `2`. No baseline in the
    wild was pinned to scheme 1, so there is no migration cost; the scheme is recorded in the v3
    `identity` block and gated by the grammar-change guard above.

- **No other breaking JSON-schema changes** were queued for this minor. The only field-level schema
  change is the additive-then-relaxed `functions[].language` (`const "python"` → `enum ["python",
  "typescript"]`) and the removal of the informational `typescript[]` array, both covered above; the
  `check`/`diff` payloads and baseline entries gain `language` as described. No deferred rename or
  type change was outstanding.

## [0.2.16] - 2026-07-18

LCOV coverage support for the experimental TypeScript track. Still **informational only** — no
scoring, no baseline, no gating, exit code unchanged. Python-only installs and the Python
analyzer/scoring are unchanged (TypeScript is reached only through `scan
--experimental-typescript` and the coverage mapping imports no tree-sitter).

### Added

- **`--ts-coverage` now accepts LCOV (`lcov.info`)** in addition to Istanbul/nyc
  `coverage-final.json` (the format most non-nyc toolchains emit — `c8 --reporter=lcov`, Karma,
  many Jest reporters, CI uploaders). The format is **auto-detected per file** (extension
  `.info`/`.lcov` or a leading `TN:`/`SF:` line → LCOV; a leading `{` → Istanbul JSON), so a single
  repeatable `--ts-coverage` list may **mix both formats** and they merge. An LCOV report is
  normalized into the same internal shape as Istanbul, so an LCOV and an Istanbul report describing
  the same measured lines produce identical coverage numbers. `--ts-coverage` remains repeatable,
  TypeScript-only, and separate from the Python `--coverage`.

### Notes

- LCOV `line_coverage` is **line-derived** (a line is measured iff it carries a `DA` record) — a
  third measurement basis distinct from Istanbul's statement-start lines and coverage.py's
  executable-line set. As before, coverage percentages are **not interchangeable across backends**
  (see `docs/language-backend-contract.md §2`) — real `c8`-LCOV and `nyc`-Istanbul reports diverge
  on the same source. LCOV `FN`/`FNDA` function hit counts and the `LF`/`LH`/`BRF`/`BRH` file totals
  have no field in `CoverageStats` and are parsed-and-ignored (whether to consume `FNDA` is an open
  0.3.0 question).
- Robustness: a UTF-8 BOM is stripped; a record whose `DA:` lines are all unparseable is **rejected**
  rather than silently read as "100% covered"; and an LCOV report saved under a `.json` name gets a
  directed "looks like an LCOV report" hint instead of an opaque JSON error.
- No schema, CLI-flag, JSON-field, SARIF, or baseline changes; all Python-output snapshots are
  byte-stable (the Istanbul code path is untouched).

## [0.2.15] - 2026-07-11

Slice 5 of the experimental TypeScript track: **native JSON/SARIF output** and **token-stable
identity** for discovered TypeScript functions. Still **informational only** — no scoring, no
baseline, no gating, exit code unchanged. Python-only installs and the Python analyzer/scoring are
unchanged (TypeScript is reached only through `scan --experimental-typescript` and imports
tree-sitter only via the opt-in `typescript` extra).

### Added

- (P20, slice 5) **TypeScript in `scan --json`**: a new additive top-level `typescript` array of
  unscored functions — `path`, `qualname`, `language: "typescript"`, `kind`, `is_public`,
  `complexity`, line/branch coverage, `lines`, and identity `fingerprint`/`signature`. No
  `score`/`components` (TypeScript is informational until 0.3.0). The key is **omitted** without
  the flag, so the Python contract and all snapshots are byte-stable. Schema: `report.schema.json`
  gains `$defs/ts_function`.
- (P20, slice 5) **TypeScript in `scan --format sarif`**: each discovered function is emitted as an
  informational `level: "note"` result under a new `riskratchet.typescript-function` rule
  (registered only when TypeScript results are present), tagged `language: "typescript"` in
  `properties`. Note this means **one `note` per discovered function** — an inventory carried in
  `results`, not defect findings; an accepted tradeoff for this experimental, opt-in surface.
- (P20, slice 5) **Token-stable identity** (`typescript_identity.py`): a body and a signature
  fingerprint per function — a lossy structural hash **analogous to** the Python backend's contract,
  stable across formatter whitespace/quote/semicolon/paren choices and sensitive to real
  body/signature edits. It is groundwork for rename-aware scoring at 0.3.0 (the matcher is already
  language-neutral and is *intended* to consume these), but nothing scores or gates on it yet, the
  format is **experimental and not frozen**, and it is scheme-versioned (`SCHEME_VERSION`) and tied
  to the tree-sitter-typescript grammar version — so it must be re-validated and the grammar pinned
  before any 0.3.0 baseline stores it.

### Fixed

- (SARIF) Scored Python function results now carry `properties.language` (added additively in
  0.2.11 to the JSON payload but never to SARIF) and `properties.group`, so the two machine formats
  match.

### Changed

- (experimental TypeScript) With `--json` / `--format sarif`, the discovered-function listing that
  0.2.14 printed to **stderr** is now embedded in the stdout payload instead (see above) and the
  stderr listing is suppressed for those two formats — additive on stdout, a deliberate subtraction
  on stderr for that flag combo. The human (table/markdown/…) formats keep the stderr listing.
- `function.language` in `report.schema.json` / `explain.schema.json` stays `{ "const": "python" }`:
  scored `functions[]` entries are always Python, since unscored TypeScript lives in the separate
  `typescript[]` array (`$defs/ts_function`, `language: "typescript"`). A future 0.3.0 that mixes
  scored TS into `functions[]` is what would relax it to an enum.

## [0.2.14] - 2026-07-04

Slice 4 of the experimental TypeScript track: **cyclomatic complexity** and **barrel-aware
public surface**. Still **informational only** — no scoring, no baseline, no gating, exit code
unchanged. Python-only installs and the Python analyzer/scoring are byte-for-byte unchanged
(the new code is reached only through `scan --experimental-typescript` and imports tree-sitter
only via the opt-in `typescript` extra).

### Added

- (P20, slice 4) **Cyclomatic complexity for TypeScript**: every discovered function now shows
  a McCabe `cx N` count in the `--experimental-typescript` listing (on **stderr**), computed to
  match **ESLint's `complexity` rule** (`typescript_complexity.py`) — per-function (nested
  functions pruned), counting `??` and default parameters, not counting optional chaining `?.`
  or `switch` `default`. This intentionally differs from riskratchet's Python backend in two
  ways (Python does not count default parameters and does not prune nested functions), so TS and
  Python complexity are not directly comparable — a slice-5 reconciliation item.
- (P20, slice 4) **Barrel-aware public surface**: `is_public` is narrowed to functions
  reachable from the package entry through `export … from` re-export chains (`export { x } from`,
  `export * from`, transitively). The entry is `--ts-entry` (new, repeatable), else
  `package.json` `exports`/`module`/`main`/`types` (best-effort; source-pointing fields only),
  else the shallowest `index.{ts,tsx,mts,cts}`; the driving entry is announced on stderr.
  Narrowing only demotes, and never on an unproven graph: an **unresolved `export *`** (or
  missing entry) keeps all flags, while a single **unresolved named** re-export holds only that
  name public and narrows the rest — so a third-party re-export in a barrel doesn't disable the
  feature.

### Fixed

- (TypeScript) A bare `export default myFunc;` referencing a separately-declared binding is now
  recognized as public (previously marked internal).

## [0.2.13] - 2026-06-27

0.2.13 is the "TypeScript coverage mapping" release — slice 3 of the experimental TypeScript
track. It teaches `scan --experimental-typescript` to annotate each discovered TypeScript
function with line/branch coverage read from an Istanbul/nyc `coverage-final.json`. Still
**informational only**: no scoring, no baseline, no gating, exit code unchanged. Python-only
installs are untouched — the new mapping is pure JSON (no tree-sitter), and the Python analyzer
and scoring are byte-for-byte unchanged. (This slice ships ahead of an external-demand signal —
the demand-gate clock from 0.2.12 had barely started — by maintainer choice, to keep the
TypeScript track moving while the work and fixtures were fresh.)

### Added

- (P20, slice 3) **Experimental TypeScript coverage mapping**: `scan
  --experimental-typescript --ts-coverage <coverage-final.json>` annotates the discovered-
  function listing (on **stderr**) with per-function line coverage, branch coverage, and
  missing lines, mapped from an **Istanbul/nyc** report (`nyc`/`c8`/Jest `--coverage`). Line
  coverage keys on each statement's start line (collapsing shared lines by max hit count, like
  `istanbul-lib-coverage`); branch coverage counts the arms (`if`/`switch`/`&&`/ternary/
  default-arg) of each branch inside a function's span. `--ts-coverage` is separate from Python
  `--coverage` and has no effect without `--experimental-typescript` (warned). It is
  **repeatable** — pass one report per package in a monorepo and they merge. `--json`/`--format
  sarif`/`--output` stay valid with the flags on. **LCOV is intentionally deferred** (Istanbul
  JSON only this slice).
- **Misalignment guard**: when a report's line numbers don't intersect any discovered function
  in a file — the signature of coverage collected on *compiled JS* without source-map remapping
  — the file is warned and its coverage omitted, rather than showing confidently-wrong numbers.
  A file simply absent from the report is reported explicitly (`N file(s) had no coverage
  entry`), not silently dropped.
- **Shared backend protocol** `riskratchet.models.DiscoveredFunctionLike` (`id`, `span`,
  `is_public`, `is_async`) — the structural unification of the Python
  `analysis.DiscoveredFunction` and the TypeScript `typescript.TsFunction` behind one type,
  conformance-checked statically and at runtime (`tests/test_backend_protocol.py`). Identity
  (body/signature fingerprint for rename-aware baseline matching) stays Python-only until
  TypeScript has a token-stable fingerprint — that identity half, not the shape, is what still
  blocks TS from the scoring/baseline pipeline.
- New `riskratchet.typescript_coverage` module (`load_istanbul_coverage`,
  `load_istanbul_coverage_files`, `coverage_for_ts_span`, `spans_cover_any_statement`).
  `TsFunction` gains an additive `coverage: CoverageStats | None` field, and `CoverageStats`
  gains an additive `missing_branch_arms` field — the TS `(line, arm_index)` analog of the
  Python `missing_branches` `(src_line, dst_line)` arcs, kept separate so the two are never
  confused. Note: TS `line_coverage` is statement-start-derived (Istanbul) and is **not** the
  same measurement as the Python line-level value — equal percentages must be recalibrated
  before any future cross-language scoring.

## [0.2.12] - 2026-06-20

0.2.12 is the "experimental TypeScript discovery + contract docs" release. It pulls
TypeScript slice 2 (P20) forward — the first capability to actually look at TypeScript —
and closes the remaining SARIF/config contract-doc gaps (P17/P18). Python-only installs
are unchanged: tree-sitter ships only in the new optional `typescript` extra, the Python
analyzer and scoring are byte-for-byte unchanged, and no weight or threshold moved.

### Added

- (P20) **Experimental TypeScript function discovery** behind `scan
  --experimental-typescript` — informational only (no scoring, no coverage, no baseline,
  no gating; does not affect the exit code). It lists discovered `.ts`/`.tsx`/`.mts`/`.cts`
  functions (top-level functions, class methods — including on abstract and anonymous
  default-export classes — and named arrow/function expressions; React components fall out
  as exported functions/arrows) with their qualname, public/internal surface, and line span.
  Qualnames reflect nesting through classes, functions, and `namespace`/`module` blocks, so
  a namespaced `Foo.bar` does not collide with a top-level `bar`. Public/internal is export
  reachability — inline `export`/`export default` **and** separate `export { name }` clauses.
  Files with syntax errors are skipped with a warning (never partially listed). Anonymous
  inline callbacks, object-literal methods, interface/abstract method signatures, and
  generated files (a comment-anchored `@generated` header or `*.pb.ts`/`*.gen.ts` name) are
  skipped; generator functions and async iterators are not yet supported. The listing prints
  to **stderr** (an experimental diagnostic), so `--json`/`--format sarif`/`--output` stay
  valid with the flag on; output format may change and JSON/SARIF integration is deferred to
  a later slice.
- (P20) Optional `typescript` extra: `pip install 'riskratchet[typescript]'` pulls in
  `tree-sitter` + `tree-sitter-typescript` (version-capped — `tree-sitter-typescript<0.24` —
  so a transitive lock refresh can't silently swap in a grammar whose node taxonomy the
  discovery tests assert against). A default Python-only install resolves exactly as before —
  tree-sitter is imported lazily, only on the experimental path, with a clear install hint if
  absent.

### Docs

- (P17) Documented the SARIF output contract divergence from cargo-crap explicitly:
  riskratchet always emits a schema-valid SARIF 2.1.0 document (empty `results` when the
  gate is green) rather than rejecting `baseline + sarif`. Added a `diff --format sarif`
  clean-baseline empty-results test for parity with the existing `check` test.
- (P18) Documented `riskratchet config validate` as a one-line opt-in CI strict gate (exit
  2 on unknown keys / malformed config) in the README and AGENTS.md, complementing the
  warn-by-default behavior.

## [0.2.11] - 2026-06-13

0.2.11 is the "TypeScript groundwork" release (P19). It opens the first seam for a
future TypeScript backend — a language-backend contract, a parser-strategy
decision, a static TSX fixture corpus, and one additive JSON field — **without
shipping any TypeScript scoring**. Python remains the only stable backend;
Python-only installs are unchanged (no Node dependency, same runtime dependency
closure as 0.2.10). Scoring, weights, and thresholds are byte-for-byte unchanged.

### Added

- (P19) Additive `function.language` field in `scan --json` and `explain --json`,
  always `"python"` today (declared as `{ "const": "python" }` in
  `schemas/report.schema.json` and `schemas/explain.schema.json`). It future-proofs
  the per-function payload for a `"typescript"` value; existing consumers are
  unaffected. The baseline file, SARIF, and the check/diff payloads do **not** carry
  it yet — they gain it only when TypeScript scoring ships.

### Docs

- (P19) `docs/language-backend-contract.md` — the language-neutral contract a backend
  must fill (function discovery, coverage mapping, complexity, public surface,
  function identity), with the Python implementation as the reference and the
  TypeScript open questions per area.
- (P19) `docs/typescript-parser-decision.md` — parser strategy: **tree-sitter** as an
  optional `riskratchet[typescript]` extra (no Node runtime for Python-only users),
  with the Node-backed and regex alternatives rejected and the rationale recorded.
- (P19) TSX fixture corpus under `tests/fixtures/typescript/` (top-level functions,
  class/interface methods, arrow functions, React components with hooks, default
  exports, generated-code exclusion). Checked in as a static spec for the future
  discovery slice; not exercised by gate tests.

## [0.2.10] - 2026-06-07

0.2.10 is the "supply-chain trust and calibration" release. It makes release
provenance inspectable and turns the P24 corpus tooling into a standing empirical
calibration harness — phase 1 (PR replay) plus phase 2 (SZZ defect-linking and a
predictive-validity study across 34 OSS repositories). No native payload field was
renamed or removed; scoring is byte-for-byte unchanged from 0.2.9.

### Added

- (P14) Release builds now publish supply-chain provenance. The `publish.yml`
  build job generates a CycloneDX SBOM from the declared runtime dependency
  closure (uploaded as a separate `sbom` artifact) and — the genuinely new
  control — attaches a **GitHub build-provenance attestation** to the wheel and
  sdist (`actions/attest-build-provenance`), verifiable offline with
  `gh attestation verify`. The PyPI upload's PEP 740 attestations were already
  on by default under Trusted Publishing; the workflow now sets `attestations:
  true` explicitly so the requirement is visible and survives a default change.
  See `docs/threat-model.md` for how to fetch and verify each.

### Research

- (P21) Empirical calibration harness, phase 1, under `bin/calibration/`. It
  promotes the P24 corpus tooling into a reusable harness: a per-repo corpus config
  (`data/calibration/repos/<name>/repo.toml`), PR replay with per-revision coverage (checkout
  + the repo's own suite under coverage, cached per SHA), an in-process regression
  diff (head vs base) reusing the engine's `baseline_from_report` / `diff`, and
  candidate re-scoring of the `sprawl` component against hand-labelled PR outcomes.
  The three candidates from the P24 finding — drop the file-line term, shrink its
  share, raise the 500/1000 band — are evaluated by **recomputing the component**
  (not via weight overrides, since the file-line term lives inside the blended
  sprawl score) and measuring accept/reject separation. Analysis only: **no scoring
  weight or threshold changes in this release.** Populating the rollups is a
  human-run step (see `data/calibration/README.md`); the committed rollups start
  empty.

- (P21) Empirical calibration harness, phase 2 — SZZ defect-linking + predictive
  validity, under `bin/calibration/` (`szz.py`, `fixes.py`, `defects.py`,
  `predict.py`, `git_checkout.py`). Replaces phase 1's weak hand-labelled
  accept/reject proxy with a mined outcome label: the `defects` subcommand scores
  every function at a historical snapshot, mines bug-fix commits, `git blame`s
  their deleted lines to the introducing function (reusing riskratchet's diff
  parsing, `parse_file`, and `match_rename`), and tracks it back to the snapshot;
  the `predict` subcommand reports, per sprawl candidate, the AUC of the score
  against that defect label (AUC derived from the existing `mann_whitney_u`, no new
  stats). The readout answers whether the file-line sprawl term helps or hurts
  defect prediction. Analysis only — **no scoring change**. Ships a real,
  SHA-pinned snapshot: per-function defect labels and per-candidate AUCs for **34
  enabled repos** (general libraries + a pycaret-adjacent ML cohort), each run twice
  to confirm reproducibility (labels identical on all 34, scores byte-identical on
  26). The directional finding — the score is not a reliable defect predictor on the
  largest repos, and the file-line sprawl term is net-negative on average — is
  written up in `data/calibration/defect-prediction-findings.md`; it argues *against*
  a weight change, which is why none ships.

- (P21) Empirical calibration harness, phase 3 — the pooled, repo-stratified
  logistic-regression ablation that phase 2's write-up (§6.6/§7) named as the
  decision-gate, under `bin/calibration/ablation.py` (`harness ablate` →
  `data/calibration/ablation.json`). An L2 logit with one regularized intercept per
  repo (absorbing the per-repo heterogeneity that makes a naïve pooled AUC
  meaningless), validated leave-one-repo-out, with `sprawl` split into its
  function-length and file-line halves so the file-line term gets its own coefficient.
  On the committed 34-repo / 33,490-function snapshot it **confirms the file-line
  sprawl term carries no defensible independent signal** (dropping it does not reduce
  pooled CV-AUC; sign-test p=0.024; coefficient 95% CI [−0.094, 0.248] spans zero) and
  reframes the headline: the *fitted* six-component model reaches ~0.69 within-repo
  CV-AUC even where the shipped fixed-weight score is anti-predictive, so the
  components carry signal that the current blend does not. Analysis only — **still no
  scoring change**; a 0.3.0 drop/shrink of the file-line term is now the
  model-supported front-runner, gated on the open construct / external-validity gaps.
  Uses scipy/numpy via a new **`calibration` dependency-group** — dev/research-only,
  **not** a runtime dependency (the published wheel's dependencies are unchanged).

## [0.2.9] - 2026-06-03

0.2.9 is the "structured diagnostics and privacy controls" release. It makes CI
failures debuggable without polluting stdout, lets closed-source adopters redact
identifiers from shared output, and carries a research finding on the `sprawl`
component. No native payload field was renamed or removed; non-redacted,
non-verbose output is byte-for-byte unchanged from 0.2.8.

### Added

- (P11) Structured diagnostics on `scan`, `baseline`, `check`, `diff`, and
  `explain`:
  - `--verbose` prints a small fixed set of run diagnostics to **stderr** —
    coverage source (single / map / auto, including whether the auto-coverage
    cache was reused or regenerated), git/churn settings, include/exclude/allow
    filter effects, the analysis tallies, and (for `check`/`diff`) the resolved
    baseline path and entry count.
  - `--debug-json` emits the same diagnostics as a schema-versioned JSON
    envelope to stderr; `--debug-json-file PATH` writes it to a file instead.
    The envelope is its own contract, validated against the new
    `schemas/debug.schema.json` (`version: 1`), independent of the native
    payload schemas.
  - Stdout stays payload-only regardless of either flag. When redaction is
    active, the diagnostics surfaces (the always-on banner, `--verbose`, and
    `--debug-json`) hash their path fields too, so `--private-comment` cannot
    leak paths through diagnostics.
  - Implementation note: these diagnostics are assembled at the CLI layer from
    the report fields and resolved config (not threaded through `analyze`), and
    the category set is deliberately small (~5). Git timing / commit counts were
    considered and intentionally left out until a real debugging need appears.
- (P12) Privacy-aware output redaction on `scan`, `check`, `diff`, and
  `explain`:
  - `--redact-paths` and `--redact-qualnames` replace source paths and function
    qualnames with deterministic hashes across every output format (table,
    markdown, PR comment, GitHub annotations, SARIF, and JSON), including
    inside `reason` strings so a matched-rename or ambiguous-rename note can't
    leak the original target.
  - `--private-comment` is a preset: redact paths + qualnames and suppress
    source links.
  - **Salt:** `--redact-salt TEXT`, then `RISKRATCHET_REDACT_SALT`, then
    `[tool.riskratchet] redact_salt`. When none is set, the salt is **derived
    from the commit** (`GITHUB_REPOSITORY`@`GITHUB_SHA`, else `git rev-parse
    HEAD`); only when there is no salt source at all does riskratchet warn that
    the hashes are guessable. Consequence: hashes are deterministic within a
    commit (scan/check/diff in one CI run correlate) and intentionally
    unlinkable across commits and repositories. An explicit salt overrides this.
  - Redaction is an output transform applied after baseline matching, so the
    ratchet decision (regressions, exit code, MOVED matches) is **invariant**:
    a redacted run gates identically to an un-redacted one. The persisted
    baseline file is never redacted — the `baseline` command does not accept
    redaction flags.

### Research

- (P24) Sprawl-component validation. A reproducible multi-repo experiment
  (`bin/experiments/sprawl_vs_complexity.py --clone`; rolled-up results in
  `data/calibration/sprawl-experiment.json`) over ~3,900 functions in 4 repos
  (riskratchet, requests, httpx, rich) finds the per-file `sprawl` component is
  **near-orthogonal** to `structural_complexity` (pooled Spearman ≈ 0.07) — it
  measures size, not branching — and that its function-length half fires for
  only ~1% of functions, so in practice sprawl is the *file-line* term: a
  file-level property that shifts a function's score by up to 5 points on file
  size alone. Engaging the literature (El Emam et al. 2001 on size as a
  confound; Fenton & Neil 1999; Lanza & Marinescu 2006 on God-Class
  thresholds), the finding (`docs/sprawl-component-finding.md`) judges this a
  real but file-level signal whose maintainability validity is unproven, and
  **not unambiguous enough** to retune in a patch release. No weight or scoring
  change ships in 0.2.9; the question, with concrete candidate fixes, feeds the
  P21 calibration thread. (This corrects an earlier single-repo pass that
  reported a misleadingly high r≈0.28 using Pearson on saturated data.)

### Changed

- The dogfood baseline (`.riskratchet.json`) was regenerated. The diagnostics +
  redaction wiring grew `cli.py`, which raised the `sprawl` file-line term for
  the functions in it — itself a live instance of the P24 artifact above. Cheap
  simplifications were applied first (helper extraction); the residual bump is
  the file-size sprawl effect, not genuine added complexity. `cli.py` keeps
  growing each release; splitting it is deferred (working-rule #3) but noted as
  recurring tension.

## [0.2.8] - 2026-05-30

0.2.8 is the "first 5 minutes + adoption surface + PR review" release.
Three sub-themes:
1. **First 5 minutes** — `init`, `doctor`, zero-flag `scan` UX, and
   remediation-form setup errors so a fresh user gets value without
   reading docs.
2. **Adoption surface** — reusable GitHub Action (root `action.yml`)
   + `--fail-above N` no-baseline gate so a stranger can try
   riskratchet on a public repo without committing to a baseline.
3. **PR review** — PR-comment renderer parity, `explain --json`,
   and source-link parity (every JSON renderer + SARIF + the table
   footer) so review surfaces share one envelope shape.

### Added

- (P13) `riskratchet doctor` — six setup checks (paths, baseline,
  coverage + freshness, git, config, suppressions) reported as a
  colored text table on stdout with copy-pasteable remediation on
  stderr. Exit `0` only when every check is pass or warn; a single
  fail exits `1`. `--json` emits a structured envelope validated
  against `schemas/doctor.schema.json` (Draft 2020-12). New module
  `src/riskratchet/doctor.py` exposes `diagnose()`, `summarize()`,
  `DoctorCheck`, and `CheckStatus` for embedded use.
- (P15) `riskratchet init` — scaffolds a starter `[tool.riskratchet]`
  block in `pyproject.toml` (creates, appends, or no-ops based on
  existing content; `--force` replaces an existing block in place,
  intentionally dropping `[tool.riskratchet.*]` subtables). Detects
  the test runner (pytest / unittest / unknown) and prints a CI
  snippet pinned to the `ACTION_REF` release tag. New
  `--with-baseline` flag (and interactive TTY prompt when pytest is
  detected) runs `pytest --cov` + creates a baseline as part of
  `init`. `--no-snippet` suppresses the CI snippet for scripted
  use. The CI snippet SHA-pins `actions/checkout` to v4.2.2.
- (P25) Zero-flag `riskratchet scan` next-step footer — on default
  table format (no `--quiet`, no `--summary`, no `--output`, no
  baseline file), prints a stdout footer adapted to two axes:
  (a) whether `[tool.riskratchet]` exists (otherwise lead with
  `riskratchet init`) and (b) whether any function is at severity
  medium or higher (otherwise say "nothing to baseline yet"). All
  other formats / quiet modes are unchanged.
- (P26) Actionable setup errors — `Fix one of:` remediation blocks
  replace symptom-form messages for: **Missing coverage** (`baseline`
  / `check` / `diff`), **Stale coverage / auto-coverage produced
  nothing**, **No baseline**, **Malformed baseline**, **Missing
  scan path** (both CLI-argument and config-sourced). Setup errors
  stay on stderr; stdout payload contract is unchanged.
- (P27) Reusable GitHub Action — root `action.yml` (composite) so
  adopters can `uses: KayhanB21/riskratchet@v0.2.8` instead of
  copy-pasting the CI workflow. Inputs: `paths`, `coverage`,
  `baseline` (default `.riskratchet.json`), `fail-above` (default
  `60`, used when the baseline file is absent — wires to P28's
  no-baseline gate), `comment` (default `true`), `python-version`
  (default `3.12`), `riskratchet-version` (pin a PyPI release;
  default latest), `local-wheel` (install a local wheel; used by
  the dogfood workflow), `github-token` (default
  `${{ github.token }}`). Install path uses `astral-sh/setup-uv` +
  `uv tool install` (SHA-pinned). New
  `.github/workflows/dogfood-action.yml` builds the in-tree wheel
  and runs the local action against the riskratchet repo itself,
  satisfying the P27 "CI runs the action against a synthetic PR"
  acceptance criterion. The Marketplace wrapper repo
  `KayhanB21/riskratchet-action` shipped concurrently
  (`@v1` / `@v1.0.0`) and delegates to
  `KayhanB21/riskratchet@v0.2.8`; both shapes share `action.yml`
  as their source of truth.
- (P28) `check --fail-above N` — a no-baseline absolute-threshold
  gate. When `--fail-above` is given and no baseline resolves,
  every function whose current score strictly exceeds `N` is
  reported as a `kind: "above_threshold"` regression and `check`
  exits `1`. Configurable via `[tool.riskratchet] fail_above = N`
  (`(0, 100]`). New helper
  `riskratchet.baseline.regressions_above_threshold(report,
  threshold=N)` for embedding the gate without the CLI.
  `regressions.schema.json` `kind` enum additively gains
  `"above_threshold"`; `previous_score` and `delta` are `null`
  for that kind.
- (P8) PR-comment parity — `render_regressions_pr_comment` gains a
  one-line summary block (`**Regressions:** N · **New above
  threshold:** N · …`) so scan / check / diff PR comments read as
  the same family. `check --fail-above N --format pr-comment`
  (no-baseline mode) emits the regressions-only PR comment with
  the same `<!-- riskratchet-report -->` sticky marker, instead
  of exit `2` (this supersedes the P28 rejection-on-`pr-comment`
  behaviour shipped earlier in the cycle).
- (P9) `explain --json` and `explain --summary --json` — the
  previously text-only `explain` command emits a machine-readable
  envelope (`$schema`, `version`, `command`, body) matching the
  other JSON commands. `--json` alone returns the full function
  payload (same shape as a `scan --json` `functions[]` item);
  `--summary --json` returns the compact severity/score/crap
  block. New `schemas/explain.schema.json`; `summary.schema.json`
  `command` enum additively gains `"explain"`.
- (P10) Source-link parity — `--repo-url` / `--commit-ref` now
  thread through every output that lists functions:
  - **JSON renderers** (`scan --json`, `check --json`, `diff
    --json`, `explain --json`): payloads gain an optional
    `source_url` field with the standard
    `<repo>/blob/<ref>/<path>#L<start>-L<end>` shape.
  - **SARIF**: `properties.source_url` on each
    `riskratchet.function-risk` / `.regression` result. The rule-
    level `helpUri` continues to point at the project README.
  - **Table format**: a `Source:` footer below the table lists
    `{qualname:<40} {url}` lines for each row. Direct string
    writes (not Rich) so byte-stable snapshots are preserved.
  All four JSON schemas (`report`, `regressions`, `diff`,
  `explain`) add the optional field; existing consumers are
  unaffected.

### Changed

- (P10) `SourceLinks` moved from `riskratchet.reporting.markdown`
  to `riskratchet.reporting.summary` (the leaf) so the JSON / SARIF
  / text families can use it without violating the family-isolation
  rule. Re-exported from `riskratchet.reporting` — no public API
  break.
- (P26 / refactor) `riskratchet.config._resolved_paths` is now pure
  resolution: it returns paths and never exits. The existence check
  moved to a new `cli._check_paths_exist` helper called from every
  scanning command body. `config show` (inspection-only) skips the
  check by design. Restores `config.py` as a non-CLI module.
- (P28) `check` no longer hard-requires `--baseline`: with
  `--fail-above` and no baseline resolved, `check` runs in
  no-baseline mode. Both flags together: baseline gate is
  authoritative, `--fail-above` is ignored with a stderr warning.
- (P28) `check --summary` text output adds an `above_threshold=N`
  field to the per-kind summary line for parity with the JSON
  `by_kind` map. Additive; no field rename or removal.
- (P8) The composite action (`action.yml`) uses `--format
  pr-comment` in both modes; the no-baseline branch no longer
  prepends the sticky marker manually because `check` emits it
  itself.
- (P13) `doctor` remediations (the `→ fix:` lines) go to stderr so
  `doctor 2>/dev/null` filters to the status table and
  `doctor >/dev/null` filters to the actionable commands. Matches
  the P13 acceptance criterion.
- (P13) `doctor --json` omits the `remediation` field when null.
  Schema allows the omission (not in `required`); existing
  consumers handling either value are unaffected.
- `.riskratchet.json` regenerated 4× across the cycle to track
  intent-aligned scoring drift (new `--fail-above` branching,
  P25 footer, P26 setup-error helpers, source-link threading in
  reporting/text.py and the new `init`-side baseline runner). No
  baseline-relative behavior change for users not opting into the
  new flags.

### Removed

- (cleanup) Unused `CHECK_NAMES` tuple in `src/riskratchet/doctor.py`.
- (cleanup) Unused `InitResult` dataclass in
  `src/riskratchet/init.py`.

## [0.2.7] - 2026-05-28

### Added

- Config discovery: when `--config` is not given, riskratchet walks
  upward from the current directory for the nearest `pyproject.toml`
  containing `[tool.riskratchet]` and anchors to it. Relative `paths`,
  `coverage`, `coverage_map`, `coverage_cache`, and `baseline` values
  from that config resolve against the config file's directory, so
  running from a nested package directory produces the same result as
  running from the project root. `--config` still overrides discovery,
  and an explicit `--coverage` / positional paths stay relative to the
  current directory. When no `[tool.riskratchet]` ancestor exists,
  discovery falls back silently to the current directory. With
  `[tool.riskratchet]` present at multiple levels, the nearest one
  (walking up from the cwd) wins. The auto-coverage test command also
  runs from the config directory so it measures the whole project, and a
  no-arg invocation scans the current directory rather than the entire
  project rooted at the config dir.
- Unknown-key warning: `scan`, `baseline`, `check`, `diff`, and
  `explain` print a stderr warning when `[tool.riskratchet]` contains an
  unrecognized key (e.g. a typo like `fail_new_abvoe`) instead of
  silently ignoring it. The command still runs (exit 0); use
  `riskratchet config validate` for the strict (exit 2) gate. Stdout
  stays payload-only.
- A `pyproject.toml` that exists but fails to parse warns on stderr
  during discovery and is skipped, instead of being silently passed over
  (which would let a broken local config quietly fall through to an
  ancestor's config).
- `tests/test_baseline_layering.py` enforces the new `baseline/` package
  layering rule via AST parsing: the family submodules
  (`compare`, `diff`, `regressions`) import only the `io` / `classify`
  leaves, never each other.

### Changed

- Internal: `riskratchet.baseline` is now a package
  (`src/riskratchet/baseline/__init__.py`) re-exporting submodules `io`
  (JSON load/save), `compare` (the `check` gate), `diff` (full
  comparison), `regressions` (diff → failing-regression projection), and
  `classify` (shared matching ladder + component-regression policy). All
  previous `from riskratchet.baseline import …` imports continue to
  work; the family layout is an implementation detail. No user-visible
  behavior change — `check` and `diff` outputs in every `--format` are
  byte-for-byte identical to `0.2.6` (pinned by the syrupy suite). The
  rename matcher stays in the top-level `riskratchet.matching` module
  (also used by `analysis`), so it is intentionally not part of this
  package.
- Running from a nested directory now discovers and anchors to an
  ancestor `[tool.riskratchet]` config (see Config discovery). Before,
  config was read only from the current directory, so a nested
  invocation silently used no config. Pass `--config` explicitly to
  restore the old cwd-only behavior. The auto-coverage test command now
  runs from the config directory for everyone (not just nested runs); a
  project run from its own root sees no change.
- Internal: config discovery, validation, anchoring, and value
  resolution moved out of `cli.py` into a new `src/riskratchet/config.py`
  module (`cli.py` keeps only command definitions and dispatch, per the
  AGENTS.md "thin CLI" rule). No CLI behavior change.
- `.riskratchet.json` regenerated to reflect the new file paths. Part of
  the score drop on moved functions is the per-file `sprawl` component
  (smaller files score lower) rather than a genuine maintainability win;
  validating whether `sprawl` overlaps `structural_complexity` is a
  calibration item (roadmap P21), not a weight change here.

## [0.2.6] - 2026-05-26

### Changed

- Internal: `riskratchet.reporting` is now a package
  (`src/riskratchet/reporting/__init__.py`) re-exporting submodules
  `text`, `markdown`, `json_payload`, `sarif`, `annotations`, and
  `summary`. All previous `from riskratchet.reporting import …`
  imports continue to work; the family layout is an implementation
  detail. No user-visible behavior change — outputs in every
  `--format` are byte-for-byte identical to `0.2.5`.
- `.riskratchet.json` regenerated to reflect the new file paths.
  56 function definitions moved from `reporting.py` to the new
  submodules; bodies are unchanged. Score components also dropped
  for the moved functions because the file sprawl signal is
  per-file and the new submodules are roughly one-sixth the size
  of the old monolithic file.
- `reporting/text.py` deduplicates Rich Console construction via a
  shared `_make_buffered_console()` helper (replaces three
  copy-pasted call sites).

### Added

- `syrupy` adopted for snapshot testing (`>=4`, dev dependency).
  New `tests/test_reporting_snapshots.py` pins every
  `(command × format)` combination by invoking the real CLI through
  Typer's `CliRunner`, so the dispatch glue is covered alongside the
  renderers. Existing snapshot tests in `tests/test_cli_snapshots.py`
  and `tests/test_diff.py` migrated to syrupy (inline goldens
  replaced with `.ambr` snapshots under `tests/__snapshots__/`).
- `tests/test_reporting_layering.py` parses each submodule's AST
  and asserts the family-isolation rule: only `summary.py` is shared
  across `text`, `markdown`, `json_payload`, `sarif`, and
  `annotations`. Catches accidental cross-family imports.
- `tests/reporting_fixtures.py` consolidates the in-memory
  `RiskReport`/`Regression`/`DiffReport` builders previously
  duplicated across reporting tests, plus a `make_cli_project()`
  helper that drops a deterministic on-disk fixture (pyproject,
  source, coverage.json) for CliRunner-based snapshots.

## [0.2.5] - 2026-05-25

### Added

- Rename-aware baseline matching: `compare` and `diff` now recognize
  renamed/moved functions via a weighted multi-signal matcher (body
  fingerprint, signature fingerprint, path equality, qualname tail,
  component-vector proximity, score proximity). Unambiguous matches stay
  `MOVED`; multi-candidate matches surface as a new `AMBIGUOUS_RENAME`
  status that always gates so risk growth can't be silently masked.
- New `BaselineEntry.signature` (and per-function `FunctionRisk.signature`):
  a name- and location-stripped hash of arguments, decorators, and return
  annotation. Stored optionally in baseline JSON; old baselines without
  the field continue to load.
- Monorepo coverage support: `[tool.riskratchet.coverage_map]` table and
  repeatable `--coverage-map prefix=path` CLI flag on `scan`, `baseline`,
  `check`, and `diff`. Longest matching prefix wins. Mutually exclusive
  with the single `--coverage` path.
- Diagnostics banner: every command now emits one stderr line that names
  the resolved root, scan paths, and coverage source (single file or per-
  prefix map). Stdout stays payload-only.
- Top-risk dogfood report: `bin/dogfood-top-risk.sh` regenerates
  `docs/top-risk.md` and `docs/top-risk.json`. A new informational CI job
  uploads the markdown as a per-PR artifact.
- Baseline governance gate: `bin/check_baseline_rationale.py` plus the
  `.github/workflows/baseline-gate.yml` workflow fail a PR that mutates
  `.riskratchet.json` without a rationale heading, an inline
  `riskratchet-baseline-rationale:` line, the `baseline-approved` label,
  or a `[riskratchet-baseline-bypass]` commit-message token.
- `tests/fixtures/monorepo/` end-to-end fixture for the monorepo path.

### Changed

- `riskratchet scan`, `baseline`, `check`, and `diff` now treat the
  positional `paths` argument as optional; when omitted, paths are taken
  from `[tool.riskratchet] paths` (or default to `.`). Backwards
  compatible — existing invocations are unaffected.
- Diff JSON output gained additive per-entry fields `previous_targets`
  (array) and `match_confidence` (number/null), plus an `ambiguous_rename`
  count in the summary. Schema (`schemas/diff.schema.json`) updated.
- Diff PR-comment block now lists ambiguous renames in the visible
  (gating) section alongside regressions and new functions.
- `_diff_summary_line` always renders `**Ambiguous renames:** N` even
  when zero, matching the unconditional formatting of regressed / new /
  improved counts.
- `parse_rationale` in `bin/check_baseline_rationale.py` returns the
  full rationale body, not just the first line. The displayed gate
  message still abbreviates to 80 characters for readability.

### Internal

- New module `src/riskratchet/matching.py` houses `match_rename`,
  `signature_fingerprint`, and the documented similarity weights /
  threshold (kept separate from `baseline.py` to ease the 0.2.7 baseline
  split). Weights are provisional pending empirical calibration
  (roadmap P13).
- New private helper `_classify_against_baseline` in `baseline.py`
  encapsulates the exact-id → unique-fingerprint → weighted-rename
  matching ladder, consumed by both `compare` and `diff`. Brought both
  functions back near their pre-0.2.5 risk scores.
- `engine.analyze` accepts a `coverage_map` argument; `coverage_path` and
  `coverage_map` are mutually exclusive.
- `MultiCoverageData` in `coverage.py` shards `CoverageData` by repo-
  relative prefix with longest-prefix dispatch.
- New `tests/test_workflows_yaml.py` validates the new workflows
  structurally (shape, artifact upload, pin-to-SHA security posture).
- New monorepo end-to-end test verifies that per-package coverage shards
  do not bleed across packages (longest-prefix lookup is enforced).

## [0.2.4] - 2026-05-25

### Changed

- Folded release artifact metadata, install smoke, and SARIF validation checks
  into `publish.yml` so tag publishes validate the built distributions before
  uploading to PyPI.
- Added `ruff format --check .` to CI and publish quality gates.
- Removed the separate manual `release-check.yml` workflow now that publish owns
  the release validation path.
- Cleaned up dependency-audit input generation so the exported requirements omit
  the editable local project entry.
- Improved regression table ergonomics so long function targets are not
  truncated in terminal output.
- Clarified new-function findings: "new" now explicitly means absent from the
  baseline, not necessarily changed in the current commit.

### Fixed

- Added direct tests around source-tree version fallback behavior and simplified
  the fallback path so the repo's own ratchet gate stays green without
  accepting avoidable risk into the baseline.

## [0.2.3] - 2026-05-24

### Fixed

- Fixed runtime version drift by deriving `riskratchet --version` and package
  `__version__` from installed package metadata, with a source-tree fallback
  for unusual uninstalled execution.
- Fixed the README logo URL so PyPI can render the project logo from the
  package long description.
- Hardened release checks so the package metadata version, CLI `--version`,
  built wheel metadata, and wheel README metadata are verified before release.

### Added

- Focused regression tests for diff-to-regression conversion, diff renderers,
  PR-comment rendering, and SARIF regression output before broader output-path
  refactors.
- Baseline governance guidance for PRs and documentation, plus a threat model
  for coverage, baseline, supply-chain, and information-leakage limits.
- Security automation with CodeQL scanning and a pinned dependency audit
  workflow.

## [0.2.2] - 2026-05-24

### Added

- Normalized `pr-comment` output. `scan --format pr-comment` now emits a
  sticky review body with summary, current findings, source links, and
  collapsed lower-priority rows. `check --format pr-comment` now renders the
  same multi-section diff body as `diff --format pr-comment` while preserving
  the existing failing-regression exit semantics and regression-only
  `check --json` contract.
- `--summary` on `scan`, `check`, and `diff`. Plain output is compact
  aggregate lines for CI parsers; `--summary --json` emits a schema-backed
  envelope with `$schema`, `version`, `command`, and `summary` only.
- Markdown/PR source links on `scan` and `check` via `--repo-url` and
  `--commit-ref`, with GitHub Actions defaults from `GITHUB_SERVER_URL`,
  `GITHUB_REPOSITORY`, and `GITHUB_SHA`.
- `[tool.riskratchet.groups]` package/workspace rollups. Functions and diff
  entries now include additive optional `group` fields in JSON, and summaries
  include group-level counts. Ungrouped entries are `null` in JSON and
  `ungrouped` in text/markdown.
- `riskratchet config validate` and `riskratchet config show --json`, plus
  `schemas/config.schema.json` and `schemas/summary.schema.json`.
- Test coverage for the new review ergonomics: PR-comment snapshots, summary
  text/JSON behavior, source-link flags and GitHub Actions defaults, group
  longest-prefix matching, config validation/show paths, and schema
  validation for report/diff/config/summary outputs.
- README documentation for SARIF's intentional contract: scan SARIF reports
  filtered current findings, while check/diff SARIF report failing
  regressions and clean runs keep a valid empty `results` array.

### Removed

- `scripts/publish.sh`. Releases now go through `.github/workflows/publish.yml`
  (PyPI Trusted Publishing via OIDC) — tag `vX.Y.Z`, push, done. The README
  "Release" section has been updated to match.

## [0.2.1] - 2026-05-23

### Added

- `--churn-days N` flag on `scan`, `baseline`, `check`, `explain`, and
  `diff` (default `90`). Also configurable as `[tool.riskratchet]
  churn_window_days`. CLI value wins over config.
- `__all__`-aware `public_surface` classification. Module-level
  `__all__ = [...]` (static list/tuple of string literals) additively
  promotes top-level names to public — a leading-underscore class or
  function listed in `__all__` is now treated as part of the public
  surface. Omission never demotes. Dynamic `__all__` falls back to the
  qualname-based naming rule. See README "Components, in plain English"
  for the full semantics.
- `check` now prints a short hint to **stderr** when it exits with
  regressions, naming the two escape hatches: regenerate the baseline,
  or loosen `--no-component-regression-gate` /
  `--fail-component-regression-above`. The hint is conditional —
  option 2 only appears when at least one regression has `kind ==
  "component_regressed"`. Stdout stays clean for `--json` consumers.
- README "Use cases" section with four detailed scenarios (solo dev +
  AI agent, team gating PRs in CI, pre-commit hook for solo repo,
  investigating one function with `explain` + `diff`).
- README "Components, in plain English" subsection — one paragraph per
  component with a concrete numeric example, plus a worked total-score
  example.
- README pre-commit integration: new "uv / poetry projects" variant
  under Pattern A showing the all-`language: system` style this repo
  uses on itself, plus a "How pre-commit and riskratchet fit together"
  preamble explaining the stashing/venv interaction.
- GitHub Actions: new `riskratchet` job in `.github/workflows/ci.yml`
  that runs on every pull request, regenerates `coverage.json`, runs
  `check --format pr-comment`, and upserts a single sticky PR comment
  via the `<!-- riskratchet-report -->` marker. Job fails when
  regressions are detected.
- Strengthened scoring test suite: per-component boundary tests at
  saturation thresholds and severity-band edges, plus nine new
  hypothesis-driven property tests covering each of the six components
  (boundedness, saturation, monotonicity, private/public contracts).
- New `tests/fixtures/all_exports_focused/` end-to-end fixture
  exercising the `__all__` promotion path through `analyze()`.
- Configurable risk weights via `[tool.riskratchet.weights]` in
  `pyproject.toml`. Any subset of the six component keys may be
  overridden; remaining keys keep their default and the whole vector is
  renormalized so the total stays in `[0, 100]`. Unknown keys, negative
  values, or an all-zero table cause the CLI to exit `2`. See README
  "How risk is scored" for the defaults.
- README "Release" section pointing at `scripts/publish.sh` and
  describing GitHub Actions as the source of truth for package health.
- README "Local development" section now mirrors the exact command
  sequence CI runs, so local green and CI green mean the same thing.
- AGENTS.md notes that CI is the canonical check set.

- `--json` flag on `scan` and `check` as a shortcut for `--format json`.
- `--quiet` / `-q` flag on `scan` to suppress the trailing summary line
  for pipe-friendly use from CI and agents.
- JSON Schemas published under `schemas/`:
  `report.schema.json`, `regressions.schema.json`, `baseline.schema.json`.
  Validated against actual CLI output in `tests/test_schemas.py`.
- `AGENTS.md` documenting how AI coding agents should invoke the tool and
  what guarantees the output contract provides.
- README "Using riskratchet from an AI coding agent" section with
  one-shot, CI, and PR-comment recipes plus a common-mistakes list.
- `.pre-commit-hooks.yaml` now defaults to
  `--no-auto-cov --allow-missing-coverage` so the published hook works in
  pre-commit's isolated environment (no pytest available by default). The
  README now documents two intentional integration patterns: pre-generate
  coverage in a separate hook, or use `language: system` to give riskratchet
  access to your pytest.
- AGENTS.md documents the SARIF 2.1.0 output contract, including the
  driver name and rule IDs (`riskratchet.function-risk`,
  `riskratchet.regression`).
- Auto-coverage: when no usable `coverage.json` is present, riskratchet
  runs the configured `[tool.riskratchet] test_command` (default
  `pytest --cov --cov-branch --cov-report=json:{output} -q`) and caches
  the result at `.riskratchet/coverage.json`. The cache is reused while
  no source `.py` file is newer. Disable with `--no-auto-cov` or
  `auto_coverage = false`. Override the cache path with
  `coverage_cache = "..."`. `.riskratchet/` is gitignored.

## [0.2.0]

- First documented release. CLI subcommands: `scan`, `baseline`, `check`,
  `explain`. Pytest plugin available via `--riskratchet`.
- Risk score combines coverage gap, structural complexity, branch gap,
  churn, public surface, and sprawl.
- Baseline file format version `1`.
