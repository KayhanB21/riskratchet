# riskratchet report

**Functions analyzed:** 12
**Files analyzed:** 22
**Coverage:** present

| Severity | Score | CRAP | CC | LCov | BCov | Function | Lines |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| medium | 47.8 | 47.9 | 23 | 64% | 67% | `src/riskratchet/cli.py::_validate_config` | 891-931 |
| medium | 45.0 | 4.1 | 3 | 50% | 50% | `src/riskratchet/cli.py::config_show` | 139-164 |
| medium | 44.6 | 17.6 | 7 | 40% | 12% | `src/riskratchet/cli.py::_ensure_coverage_map_exists` | 1260-1286 |
| medium | 43.0 | 2.0 | 1 | 0% | n/a | `src/riskratchet/pytest_plugin.py::pytest_addoption` | 21-80 |
| medium | 42.2 | 6.0 | 2 | 0% | n/a | `src/riskratchet/models.py::RiskReport.by_id` | 137-138 |
| medium | 41.0 | 2.0 | 1 | 0% | n/a | `src/riskratchet/complexity.py::complexity_for_function` | 35-37 |
| medium | 41.0 | 2.0 | 1 | 0% | n/a | `src/riskratchet/models.py::DiffReport.ambiguous_renames` | 232-233 |
| medium | 38.9 | 11.8 | 6 | 45% | 25% | `src/riskratchet/cli.py::_resolved_churn_days` | 1247-1257 |
| medium | 37.7 | 21.0 | 21 | 98% | 100% | `src/riskratchet/engine.py::analyze` | 38-145 |
| medium | 36.5 | 16.1 | 16 | 94% | 94% | `src/riskratchet/cli.py::diff` | 594-744 |
| medium | 32.9 | 17.4 | 17 | 89% | 93% | `src/riskratchet/baseline.py::regressions_from_diff` | 259-343 |
| medium | 32.0 | 13.0 | 13 | 100% | 100% | `src/riskratchet/cli.py::check` | 383-543 |

_Generated 2026-05-27T01:59:00Z by bin/dogfood-top-risk.sh_
