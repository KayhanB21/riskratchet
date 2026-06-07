# Defect-prediction results — full 34-repo table (generated)

Consistent single-window snapshot; every repo run twice (Phase B), `repro` = byte-identical scores (`yes`) or N functions of coverage drift.
`d` = total_auc(drop_file_line) − baseline. See `defect-prediction-findings.md` for analysis.

| repo | ml | n_buggy | n_func | base_total | base_sprawl | z | drop_total | d | repro |
| --- | :-: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :-: |
| xarray |  | 224 | 7827 | 0.464 | 0.526 | -2.0 | 0.48 | +0.016 | yes |
| networkx | ml | 64 | 7083 | 0.391 | 0.57 | -3.0 | 0.404 | +0.014 | ~3fn |
| croniter |  | 55 | 248 | 0.479 | 0.72 | -0.5 | 0.356 | -0.123 | yes |
| tenacity |  | 52 | 156 | 0.502 | 0.473 | 0.0 | 0.507 | +0.005 | ~1fn |
| deepdiff |  | 41 | 346 | 0.517 | 0.525 | 0.4 | 0.523 | +0.006 | yes |
| packaging |  | 37 | 225 | 0.643 | 0.538 | 2.8 | 0.61 | -0.033 | yes |
| pint |  | 31 | 1727 | 0.375 | 0.489 | -2.6 | 0.386 | +0.011 | ~1fn |
| click |  | 28 | 526 | 0.648 | 0.542 | 2.7 | 0.664 | +0.016 | yes |
| sqlglot |  | 28 | 2396 | 0.788 | 0.562 | 5.3 | 0.792 | +0.004 | yes |
| marshmallow |  | 26 | 235 | 0.571 | 0.496 | 1.2 | 0.587 | +0.016 | yes |
| pyparsing |  | 22 | 478 | 0.575 | 0.456 | 1.2 | 0.623 | +0.048 | yes |
| rich |  | 19 | 901 | 0.614 | 0.64 | 1.7 | 0.624 | +0.01 | yes |
| bayesian-optimization | ml | 18 | 165 | 0.722 | 0.651 | 3.1 | 0.553 | -0.168 | yes |
| more-itertools |  | 17 | 249 | 0.757 | 0.496 | 3.6 | 0.757 | +0.0 | yes |
| lifelines | ml | 10 | 1295 | 0.618 | 0.635 | 1.3 | 0.709 | +0.09 | yes |
| requests |  | 10 | 240 | 0.632 | 0.52 | 1.4 | 0.615 | -0.016 | yes |
| loguru |  | 9 | 232 | 0.793 | 0.581 | 3.0 | 0.821 | +0.027 | yes |
| mlxtend | ml | 9 | 1165 | 0.847 | 0.791 | 3.7 | 0.856 | +0.009 | yes |
| pygments |  | 9 | 936 | 0.361 | 0.412 | -1.4 | 0.423 | +0.061 | ~6fn |
| markdown |  | 8 | 382 | 0.588 | 0.423 | 0.9 | 0.626 | +0.038 | yes |
| sqlparse |  | 8 | 210 | 0.731 | 0.441 | 2.3 | 0.762 | +0.032 | yes |
| cattrs |  | 7 | 273 | 0.682 | 0.516 | 1.7 | 0.754 | +0.072 | ~2fn |
| wrapt |  | 6 | 192 | 0.537 | 0.391 | 0.3 | 0.523 | -0.014 | yes |
| jsonschema |  | 5 | 646 | 0.479 | 0.429 | -0.2 | 0.549 | +0.07 | yes |
| pingouin | ml | 5 | 159 | 0.846 | 0.76 | 2.6 | 0.835 | -0.011 | yes |
| arrow |  | 4 | 175 | 0.501 | 0.559 | 0.0 | 0.614 | +0.113 | yes |
| arviz | ml | 4 | 1430 | 0.809 | 0.88 | 2.1 | 0.823 | +0.013 | ~1fn |
| category-encoders | ml | 4 | 157 | 0.76 | 0.509 | 1.8 | 0.756 | -0.003 | ~22fn |
| attrs |  | 3 | 202 | 0.73 | 0.775 | 1.4 | 0.725 | -0.004 | yes |
| werkzeug |  | 3 | 1113 | 0.53 | 0.638 | 0.2 | 0.531 | +0.001 | yes |
| flask |  | 2 | 369 | 0.83 | 0.828 | 1.6 | 0.79 | -0.039 | yes |
| formulaic | ml | 2 | 452 | 0.349 | 0.229 | -0.7 | 0.532 | +0.183 | ~1fn |
| boltons |  | 1 | 912 | 0.805 | 0.085 | 1.1 | 0.817 | +0.012 | yes |
| feature-engine | ml | 1 | 388 | 0.373 | 0.463 | -0.4 | 0.385 | +0.012 | yes |
