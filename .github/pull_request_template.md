<!-- Thanks for the PR. A short description of *why* is more useful than *what* — the diff already shows the what. -->

## Summary



## Checklist

- [ ] Tests added or updated for the changed behavior.
- [ ] `uv run ruff check . && uv run mypy src tests && uv run pytest -q` passes locally.
- [ ] No new **runtime** dependencies (or, if there is one, linked to a prior discussion).
- [ ] `CHANGELOG.md` updated under "Unreleased" if user-visible.
- [ ] If `.riskratchet.json` changed, this PR explains why the new baseline is intentionally accepted.
