# Contributing to riskratchet

Thanks for your interest. This is a small project; the bar for changes is "makes the tool more useful without making it harder to reason about."

## Development setup

```bash
# Clone, then:
uv sync --all-extras --dev
uv run pre-commit install   # optional but recommended
```

Python 3.10+ is required. CI runs against 3.10–3.14.

## Quality gates

Before opening a PR, all of these must pass locally:

```bash
uv run ruff check .
uv run mypy src tests
uv run pytest -q
```

If you touch coverage-sensitive code, also run:

```bash
uv run pytest --cov --cov-branch --cov-report=term-missing -q
```

The project's own `riskratchet check` gate runs on every PR. If your change regresses risk on existing functions, the PR comment will say so — address it or argue against it in the PR description.

If `.riskratchet.json` changes, the **Baseline gate** workflow
(`.github/workflows/baseline-gate.yml`) fails the PR unless you provide a
rationale. Use one of:

- a `## Baseline bump rationale` heading in the PR body followed by at least
  twenty characters of explanation,
- an inline `riskratchet-baseline-rationale: <text>` line in the PR body,
- the `baseline-approved` label (maintainer override), or
- a `[riskratchet-baseline-bypass]` token in any commit message between the
  base and head SHAs (use this when the bump is a side effect of an obvious
  refactor and the rationale lives in the commit message itself).

The gate intentionally has multiple escape hatches; the goal is "every
baseline bump has an audit trail," not "every PR needs a label."

## PR expectations

- One logical change per PR. If you find yourself writing "and also..." in the description, split it.
- Tests for new behavior. Bug fixes should include a regression test that fails without the fix.
- Add any user-visible change to `CHANGELOG.md` under an `## [Unreleased]` heading (create it if absent); the release commit renames it to the dated version.
- Keep the diff focused: no drive-by reformatting, no unrelated dependency bumps.
- Treat baseline bumps like lockfile changes: review them deliberately, and do
  not use them as a way to hide an accidental regression.

## Dependencies

Adding a new **runtime** dependency requires prior discussion in an issue. The current runtime set (`radon`, `coverage`, `typer`, `rich`, `tomli` on 3.10) is deliberately small; every addition is a long-term maintenance and supply-chain commitment.

Adding a new **dev** dependency is fine — open the PR.

## Workflow and release files

Changes to `.github/workflows/`, `pyproject.toml`'s build configuration, or anything that affects the published artifact require maintainer review (enforced via `CODEOWNERS`). First-time contributors' workflow runs may require manual approval — this is a GitHub policy, not personal.

When pinning a new GitHub Action, use the full commit SHA with the semver tag as a trailing comment:

```yaml
uses: owner/repo@<40-char-sha>  # v1.2.3
```

### Post-PR workflow smoke check

When a PR touches `.github/workflows/baseline-gate.yml`, the new `top-risk`
job in `ci.yml`, or any other workflow file, the structural YAML tests in
`tests/test_workflows_yaml.py` will catch syntactic issues — but only
opening the PR exercises the workflow against the real GitHub Actions
runner. Before flipping the PR out of draft:

1. Open it as a draft against `master`.
2. Confirm that the `baseline-rationale` and `top-risk` jobs both report
   green (the latter is informational; it must succeed for the artifact
   upload to land).
3. If either fails for environment reasons (missing dependency, runner
   version, etc.), file an issue and resolve before tagging the release.

## Releasing (maintainers)

The authoritative, complete checklist lives in [`AGENTS.md`](AGENTS.md) under "Cutting a release" —
follow it, not a summary, because `tests/test_release_integrity.py` enforces several of the steps in
CI. In brief, it is a **dedicated release commit** that bumps `pyproject.toml` + `uv.lock`, the
`test_release_integrity.py` version pin, `ACTION_REF` in `src/riskratchet/init.py`, the README
`uses:`/`rev:` pins, and the `CHANGELOG.md` date; then tag `vX.Y.Z` on `master` (→ `publish.yml`
Trusted Publishing, with a manual PyPI approval gate); then bump the `riskratchet-action` wrapper
(delegated ref, a new `v1.0.N` tag, force-move `v1`) once PyPI shows the new version.

## Questions

Open a GitHub Discussion or issue. For security issues, see `SECURITY.md`.
