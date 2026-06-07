"""Empirical calibration harness (P21).

Phase 1 (0.2.10) promotes the P24 sprawl investigation
(``bin/experiments/sprawl_vs_complexity.py``) into a reusable harness: a corpus
config, PR-replay capture with per-revision coverage, and candidate re-scoring of
the ``sprawl`` component against labelled PR outcomes. It changes no product
weights — re-scoring is analysis-only. See ``docs/riskratchet-0.2x-roadmap.md``.
"""
