# Changelog

All notable changes to `riskratchet` are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

JSON-output stability policy (see [`AGENTS.md`](AGENTS.md)): field names
in `scan --json`, `check --json`, and the baseline file are stable within
a minor version. Additive changes (new optional fields) may land in any
release; renames or removals are called out below under **Breaking**.

## [Unreleased]

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
