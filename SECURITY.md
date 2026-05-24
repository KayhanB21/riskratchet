# Security Policy

## Supported versions

`riskratchet` is currently in alpha. Only the latest released minor version on PyPI receives security fixes.

| Version | Supported |
| ------- | --------- |
| 0.2.x   | ✅        |
| < 0.2   | ❌        |

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security reports.

Two private channels are accepted:

1. **GitHub private vulnerability reporting** — preferred. Use the "Report a vulnerability" button on the Security tab of this repository.
2. **Email** — `me@kayhan.dev`. Encrypt with PGP if you want; send the unencrypted report otherwise.

Please include:

- A description of the issue and its impact.
- Steps to reproduce, or a proof-of-concept.
- The version (`riskratchet --version`) and Python version you tested against.

## Response expectations

This is a hobby-scale project maintained by one person. Best-effort response within 7 days; a fix or mitigation plan within 30 days for confirmed issues. Coordinated disclosure is welcome — propose a timeline in your initial report and we'll align on it.

## Scope

In scope:

- The `riskratchet` package as published on PyPI.
- The CLI, pytest plugin, and any code under `src/riskratchet/`.
- The GitHub Actions workflows under `.github/workflows/` (CI/publish supply-chain issues).

Out of scope:

- Vulnerabilities in transitive dependencies — report those upstream. We'll bump the pin once a fix is available.
- Issues that require a pre-compromised local machine (e.g., "if an attacker can edit my `pyproject.toml`...").
