"""Renderers for RiskReport and Regression lists.

This package replaces the single `reporting.py` module that shipped
through `0.2.5`. The public surface is preserved exactly via re-export
from the family submodules:

- `text`        — Rich tables and plain-text rendering.
- `markdown`    — Markdown and PR-comment rendering; owns `SourceLinks`.
- `json_payload` — native JSON output.
- `sarif`       — SARIF 2.1.0 log construction.
- `annotations` — GitHub Actions workflow annotations.
- `summary`     — shared payload builders and cell-level formatters.

External callers should keep importing from `riskratchet.reporting`;
the submodule layout is an implementation detail.
"""

from __future__ import annotations

from riskratchet.reporting.annotations import (
    render_diff_github,
    render_regressions_github,
    render_report_github,
)
from riskratchet.reporting.json_payload import (
    render_diff_json,
    render_diff_summary_json,
    render_function_json,
    render_function_summary_json,
    render_regressions_json,
    render_regressions_summary_json,
    render_report_json,
    render_report_summary_json,
)
from riskratchet.reporting.markdown import (
    render_diff_markdown,
    render_diff_pr_comment,
    render_regressions_markdown,
    render_regressions_pr_comment,
    render_report_markdown,
    render_report_pr_comment,
)
from riskratchet.reporting.sarif import (
    _sarif_level_for_severity,
    render_regressions_sarif,
    render_report_sarif,
)
from riskratchet.reporting.summary import (
    DIFF_SCHEMA_URL,
    EXPLAIN_SCHEMA_URL,
    OUTPUT_VERSION,
    PR_COMMENT_MARKER,
    REGRESSIONS_SCHEMA_URL,
    REPORT_SCHEMA_URL,
    SUMMARY_SCHEMA_URL,
    SourceLinks,
)
from riskratchet.reporting.text import (
    render_diff_summary_text,
    render_diff_table,
    render_function_explanation,
    render_regressions_summary_text,
    render_regressions_table,
    render_report_summary_text,
    render_report_table,
)

__all__ = [
    "DIFF_SCHEMA_URL",
    "EXPLAIN_SCHEMA_URL",
    "OUTPUT_VERSION",
    "PR_COMMENT_MARKER",
    "REGRESSIONS_SCHEMA_URL",
    "REPORT_SCHEMA_URL",
    "SUMMARY_SCHEMA_URL",
    "SourceLinks",
    "_sarif_level_for_severity",
    "render_diff_github",
    "render_diff_json",
    "render_diff_markdown",
    "render_diff_pr_comment",
    "render_diff_summary_json",
    "render_diff_summary_text",
    "render_diff_table",
    "render_function_explanation",
    "render_function_json",
    "render_function_summary_json",
    "render_regressions_github",
    "render_regressions_json",
    "render_regressions_markdown",
    "render_regressions_pr_comment",
    "render_regressions_sarif",
    "render_regressions_summary_json",
    "render_regressions_summary_text",
    "render_regressions_table",
    "render_report_github",
    "render_report_json",
    "render_report_markdown",
    "render_report_pr_comment",
    "render_report_sarif",
    "render_report_summary_json",
    "render_report_summary_text",
    "render_report_table",
]
