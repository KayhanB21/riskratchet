- [x] IDE / editor integration: SARIF docs path (VS Code + JetBrains).
- [ ] IDE / editor integration: native LSP server + thin VS Code client.
      Gate on real user demand.
- [x] Multi-language support beyond Python — **TypeScript supported (scored)**: discovery,
      coverage (Istanbul + LCOV), complexity, public surface, JSON/SARIF, and identity shipped
      across 0.2.11–0.2.16; scoring, baseline (v3), and `check`/`diff` gating landed in 0.3.0 via
      `--typescript`. Promoted ahead of the "external user in CI" demand gate by maintainer waiver
      (recorded in the 0.3.0 changelog). Future language backends remain demand-gated.
