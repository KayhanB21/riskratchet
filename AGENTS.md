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

## Exit codes

| Code | Meaning |
| ---: | --- |
| `0` | success; for `check`, no regressions |
| `1` | for `check`: at least one regression past tolerance |
| `2` | usage error (missing baseline, unknown format, unknown function) |

`scan`, `baseline`, and `explain` never exit with `1`. They exit `0` on
success and `2` on usage errors.

## Output contract and stability

- JSON schemas live in [`schemas/`](schemas/): `report.schema.json`,
  `regressions.schema.json`, `baseline.schema.json`. Each is exercised
  against real CLI output in `tests/test_schemas.py`.
- `--format sarif` emits a SARIF 2.1.0 log. The output references
  `https://json.schemastore.org/sarif-2.1.0.json` in its `$schema` field;
  the upstream OASIS definition is the [SARIF 2.1.0 spec](https://docs.oasis-open.org/sarif/sarif/v2.1.0/cs01/sarif-v2.1.0-cs01.html).
  riskratchet does not ship a separate SARIF schema. Driver name is
  `riskratchet`; rule IDs are `riskratchet.function-risk` (from `scan`) and
  `riskratchet.regression` (from `check`).
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

## CI is the source of truth

GitHub Actions runs the canonical check set. If you are unsure whether a
change is safe, run the same commands the workflow runs (see the README
Local development section) rather than inventing a local approximation.

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

- CLI entry: `src/riskratchet/cli.py`
- Scoring: `src/riskratchet/scoring.py`
- Renderers (table / JSON / markdown): `src/riskratchet/reporting.py`
- Baseline I/O and comparison: `src/riskratchet/baseline.py`
- Pytest plugin: `src/riskratchet/pytest_plugin.py`
- Schemas: `schemas/`
