# riskratchet

A maintainability ratchet for AI-assisted Python.

riskratchet computes a function-level risk score from coverage gaps,
cyclomatic complexity, churn, public surface, and sprawl signals, then fails
when a change pushes risk upward without adding tests. Use it as a CLI, in
pre-commit, or in CI so AI-assisted commits can land working code without
ratcheting up untested complexity.

## Quickstart

```bash
uvx riskratchet scan src --coverage coverage.json
uvx riskratchet baseline src --coverage coverage.json --output .riskratchet.json
uvx riskratchet check src --coverage coverage.json --baseline .riskratchet.json
```

## Local development

```bash
uv sync
uv run pytest
uv run riskratchet scan src --coverage coverage.json
```

This package is in early development. See PLAN.md for the v1 roadmap.
