# riskratchet threat model

`riskratchet` is a maintainability ratchet, not a security scanner, proof of
correctness, or replacement for tests and review. This document names the main
trust boundaries so teams can decide where the tool is useful and where other
controls are still required.

## Assets

- Baseline integrity: `.riskratchet.json` records the accepted risk floor.
- Coverage integrity: `coverage.json` determines how much uncovered code
  contributes to risk.
- Review integrity: CLI output, PR comments, GitHub annotations, SARIF, and
  JSON summaries influence what reviewers inspect.
- Release integrity: package metadata, wheels, and GitHub Actions workflows
  determine what users install and run.

## Risks and mitigations

### Stale or shallow coverage

Risk scores can look better than reality when coverage is stale, omits branch
coverage, excludes files unintentionally, or comes from a test run that skipped
important paths. Run coverage in the same CI job as the ratchet check, prefer
branch coverage, and treat missing or surprising coverage as a build problem.

### Baseline rubber-stamping

The baseline is the accepted state, not a waiver mechanism. Regenerating it
after a regression can normalize worse code. Baseline changes should be reviewed
like dependency lockfile changes and should explain why the new bar is accepted.

### Generated, vendored, and framework code distortion

Generated code, vendored code, migrations, and framework glue can dominate risk
scores without representing maintainability debt the team intends to manage.
Use `exclude` for source that should not be analyzed and `--allow` for narrow
function/path suppressions that should still leave the rest of the file visible.

### Public/private API heuristic limits

`is_public` is derived statically from naming and static `__all__` exports. It
does not know package-specific API promises, runtime export mutation, framework
registration, or external callers. Treat public-surface findings as review
signals, not as API truth.

### CI and release supply chain

Workflow compromise, unpinned actions, compromised dependencies, or a bad tag can
undermine release trust. Keep third-party actions pinned by full SHA, publish via
trusted publishing, run release checks against built artifacts, and keep
dependency and code-scanning automation enabled.

### Information leakage

PR comments, SARIF, GitHub annotations, JSON, and Markdown output can reveal
repo-relative paths, qualified names, line numbers, grouping names, and risk
reasons. Do not post these outputs to public systems for private repositories
without confirming that the metadata is acceptable to disclose.

## Non-goals

`riskratchet` does not prove code is correct, secure, performant, or readable. It
does not replace unit tests, integration tests, static analyzers, dependency
audits, secret scanning, architecture review, or human judgment. A low score
means the measured maintainability signals did not exceed the configured bar.
It does not mean the change is safe.
