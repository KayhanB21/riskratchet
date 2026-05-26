# riskratchet report

**Functions analyzed:** 25
**Files analyzed:** 16
**Coverage:** present

| Severity | Score | CRAP | CC | LCov | BCov | Function | Lines |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| high | 53.2 | 6.0 | 2 | 0% | 0% | `src/riskratchet/cli.py::_validate_format` | 823-825 |
| high | 50.0 | 90.0 | 9 | 0% | n/a | `src/riskratchet/matching.py::match_rename` | 87-131 |
| medium | 47.2 | 4.5 | 3 | 44% | 50% | `src/riskratchet/cli.py::config_show` | 139-164 |
| medium | 43.8 | 20.0 | 4 | 0% | n/a | `src/riskratchet/matching.py::signature_fingerprint` | 69-84 |
| medium | 43.5 | 17.3 | 16 | 83% | 73% | `src/riskratchet/cli.py::diff` | 579-721 |
| medium | 43.0 | 2.0 | 1 | 0% | n/a | `src/riskratchet/pytest_plugin.py::pytest_addoption` | 21-80 |
| medium | 42.2 | 31.3 | 23 | 75% | 75% | `src/riskratchet/cli.py::_validate_config` | 868-908 |
| medium | 42.2 | 6.0 | 2 | 0% | n/a | `src/riskratchet/coverage.py::load_coverage_map` | 119-122 |
| medium | 42.2 | 6.0 | 2 | 0% | n/a | `src/riskratchet/models.py::RiskReport.by_id` | 137-138 |
| medium | 41.2 | 110.0 | 10 | 0% | n/a | `src/riskratchet/matching.py::_similarity` | 134-152 |
| medium | 41.0 | 2.0 | 1 | 0% | n/a | `src/riskratchet/complexity.py::complexity_for_function` | 35-37 |
| medium | 38.8 | 72.0 | 8 | 0% | n/a | `src/riskratchet/matching.py::_component_similarity` | 159-174 |
| medium | 38.2 | 8.7 | 4 | 33% | n/a | `src/riskratchet/reporting.py::render_report_github` | 212-214 |
| medium | 36.8 | 21.0 | 21 | 97% | 100% | `src/riskratchet/engine.py::analyze` | 38-145 |
| medium | 36.3 | 20.0 | 20 | 98% | 100% | `src/riskratchet/baseline.py::compare` | 71-214 |
| medium | 36.2 | 19.2 | 19 | 92% | 88% | `src/riskratchet/baseline.py::diff` | 217-345 |
| medium | 33.3 | 11.3 | 7 | 56% | 50% | `src/riskratchet/cli.py::_resolve_source_links` | 1096-1106 |
| medium | 33.2 | 2.5 | 2 | 50% | 33% | `src/riskratchet/cli.py::_write` | 815-820 |
| medium | 33.2 | 13.1 | 13 | 91% | 75% | `src/riskratchet/reporting.py::render_report_pr_comment` | 159-202 |
| medium | 31.2 | 6.0 | 2 | 0% | n/a | `src/riskratchet/analysis.py::_has_hidden_parent` | 253-254 |
| medium | 31.0 | 2.0 | 1 | 0% | n/a | `src/riskratchet/analysis.py::_FunctionCollector.visit_AsyncFunctionDef` | 179-180 |
| medium | 31.0 | 2.0 | 1 | 0% | n/a | `src/riskratchet/analysis.py::_FunctionCollector.visit_FunctionDef` | 176-177 |
| medium | 30.8 | 13.0 | 13 | 100% | 100% | `src/riskratchet/cli.py::check` | 373-528 |
| medium | 30.0 | 2.0 | 1 | 0% | n/a | `src/riskratchet/matching.py::_component_vector` | 177-185 |
| medium | 30.0 | 2.0 | 1 | 0% | n/a | `src/riskratchet/matching.py::_qualname_tail` | 155-156 |

_Generated 2026-05-25T18:57:44Z by bin/dogfood-top-risk.sh_
