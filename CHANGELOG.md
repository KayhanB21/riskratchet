# Changelog

All notable changes to `riskratchet` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

JSON-output stability policy (see [`AGENTS.md`](AGENTS.md)): field names
in `scan --json`, `check --json`, and the baseline file are stable within
a minor version. Additive changes (new optional fields) may land in any
release; renames or removals are called out below under **Breaking**.

## [Unreleased]

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
