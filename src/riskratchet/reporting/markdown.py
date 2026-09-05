"""Markdown and PR-comment renderers."""

from __future__ import annotations

from riskratchet.models import (
    DiffEntry,
    DiffReport,
    DiffStatus,
    FunctionId,
    FunctionRisk,
    Regression,
    RiskReport,
    Severity,
)
from riskratchet.reporting.summary import (
    PR_COMMENT_MARKER,
    SourceLinks,
    _branch_markdown,
    _diff_context_line,
    _diff_summary_line,
    _fmt_optional,
    _regressions_summary_line,
    _sorted_by_risk,
    _summary_line,
    _summary_payload,
    baseline_line,
)
from riskratchet.scoring import severity

# Rows shown inside a collapsed `<details>` block before the rest are summarized.
_PR_COMMENT_ROW_CAP = 20

# GitHub rejects an issue-comment body over 65,536 characters with a 422, which
# fails the Action's upsert step (`action.yml`, `set -euo pipefail`). Budget
# headroom for the truncation notice and for GitHub's CRLF normalization.
_PR_COMMENT_MAX_CHARS = 60_000

_TRUNCATION_NOTICE = "_... truncated to fit GitHub's comment size limit._"

_DIFF_HEADER = [
    "| Status | Function | Before | After | Delta | Reason |",
    "| --- | --- | ---: | ---: | ---: | --- |",
]

# Every `DiffStatus`, so attaching a diff as context beneath a gate result can
# never silently drop one: a status missing here would vanish from the comment.
_DIFF_CONTEXT_SECTIONS = (
    (DiffStatus.NEW, "New functions"),
    (DiffStatus.REGRESSED, "Regressed within tolerance"),
    (DiffStatus.COMPONENT_REGRESSED, "Component regressions"),
    (DiffStatus.AMBIGUOUS_RENAME, "Ambiguous renames"),
    (DiffStatus.IMPROVED, "Improvements"),
    (DiffStatus.MOVED, "Moved functions"),
    (DiffStatus.REMOVED, "Removed functions"),
    (DiffStatus.UNCHANGED, "Unchanged functions"),
)


def _collapsed_section(
    rows: list[str],
    *,
    title: str,
    header: list[str],
    cap: int = _PR_COMMENT_ROW_CAP,
) -> list[str]:
    """Render pre-built `rows` as a collapsed `<details>` block, capped at `cap`.

    Taking already-rendered row strings lets the report, regression, and diff
    renderers share this despite their different column shapes.
    """
    lines = ["", f"<details><summary>{title} ({len(rows)})</summary>", "", *header]
    lines.extend(rows[:cap])
    if len(rows) > cap:
        lines.append(f"_... {len(rows) - cap} more hidden._")
    lines.extend(["", "</details>"])
    return lines


def _fit_pr_comment(body: str) -> str:
    """Trim `body` to GitHub's comment-size limit on a line boundary.

    A row cap alone is not enough: `--limit 0` legitimately disables it, and a
    repo with thousands of findings (or very long qualnames) would still exceed
    the limit and fail the Action rather than post a partial report. The marker
    on line 1 is preserved so the sticky-comment upsert still matches.
    """
    if len(body) <= _PR_COMMENT_MAX_CHARS:
        return body
    budget = _PR_COMMENT_MAX_CHARS - len(_TRUNCATION_NOTICE) - 2
    kept: list[str] = []
    used = 0
    for line in body.split("\n"):
        if used + len(line) + 1 > budget:
            break
        kept.append(line)
        used += len(line) + 1
    # Close any `<details>` the cut left open, or GitHub renders the rest collapsed.
    unclosed = "\n".join(kept).count("<details>") - "\n".join(kept).count("</details>")
    kept.extend(["</details>"] * max(unclosed, 0))
    kept.append(_TRUNCATION_NOTICE)
    return "\n".join(kept) + "\n"


def render_report_markdown(
    report: RiskReport,
    *,
    limit: int | None = 20,
    links: SourceLinks | None = None,
) -> str:
    sorted_fns = _sorted_by_risk(report.functions)
    displayed = sorted_fns if limit is None else sorted_fns[:limit]
    summary = _summary_payload(report)
    lines = [
        "# riskratchet report",
        "",
        # `len(report.functions)` is the *emitted* count, which `--top`/`--limit`
        # truncate: on a 400-function repo `--top 5` read "Functions analyzed: 5"
        # while `--json` on the same run reported 400. This was also the only
        # renderer that never disclosed suppressed or skipped functions.
        f"**Functions analyzed:** {summary['analyzed_functions']}",
        f"**Functions emitted:** {summary['emitted_functions']}",
        f"**Files analyzed:** {summary['total_files']}",
        f"**Coverage:** {summary['coverage_status']}",
        f"**Suppressed:** {summary['suppressed_functions']}",
        f"**Skipped (missing coverage):** {summary['skipped_missing_coverage']}",
        f"**Skipped (generated files):** {summary['skipped_generated_files']}",
        "",
        "| Severity | Score | CRAP | CC | LCov | BCov | Function | Lines |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |",
    ]
    for fn in displayed:
        lines.append(_markdown_row(fn, links=links))
    if limit is not None and len(sorted_fns) > limit:
        lines.append("")
        lines.append(f"_... {len(sorted_fns) - limit} more functions hidden._")
    return "\n".join(lines) + "\n"


def render_report_pr_comment(
    report: RiskReport,
    *,
    limit: int | None = 20,
    links: SourceLinks | None = None,
) -> str:
    sorted_fns = _sorted_by_risk(report.functions)
    high_priority = [fn for fn in sorted_fns if severity(fn.score) in {Severity.HIGH, Severity.CRITICAL}]
    if not high_priority:
        high_priority = sorted_fns[: limit or len(sorted_fns)]
    lower_priority = [fn for fn in sorted_fns if fn not in high_priority]
    displayed = high_priority if limit is None else high_priority[:limit]
    lines = [
        PR_COMMENT_MARKER,
        "# riskratchet",
        "",
        _summary_line(report),
        "",
    ]
    if displayed:
        lines.extend(
            [
                "| Severity | Score | CRAP | CC | LCov | BCov | Group | Function | Lines |",
                "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: |",
            ]
        )
        lines.extend(_markdown_row(fn, links=links, include_group=True) for fn in displayed)
    else:
        lines.append("_No functions emitted._")
    hidden_high_priority = high_priority[len(displayed) :]
    collapsed = hidden_high_priority + lower_priority
    if collapsed:
        lines.extend(
            _collapsed_section(
                [_markdown_row(fn, links=links, include_group=True) for fn in collapsed],
                title="Lower-priority findings",
                header=[
                    "| Severity | Score | CRAP | CC | LCov | BCov | Group | Function | Lines |",
                    "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: |",
                ],
            )
        )
    return _fit_pr_comment("\n".join(lines) + "\n")


def render_regressions_markdown(
    regressions: list[Regression],
    *,
    links: SourceLinks | None = None,
    diff_report: DiffReport | None = None,
) -> str:
    tail = _baseline_markdown_lines(diff_report)
    if not regressions:
        return "\n".join(["_No risk regressions detected._", *tail]) + "\n"
    lines = [
        "# riskratchet regressions",
        *tail,
        "",
        "| Kind | Function | Before | After | Delta | Reason |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for reg in regressions:
        lines.append(_regression_markdown_row(reg, links=links))
    return "\n".join(lines) + "\n"


def _baseline_markdown_lines(diff_report: DiffReport | None) -> list[str]:
    """A blank line and the italic baseline line, or nothing without a diff."""
    line = baseline_line(diff_report)
    return ["", f"_{line}_"] if line else []


def _diff_context_sections(
    report: DiffReport,
    *,
    gated: frozenset[FunctionId],
    links: SourceLinks | None,
) -> list[str]:
    """Render a diff as collapsed context beneath a gate result.

    Entries the gate already listed are dropped, so every function in the diff
    appears exactly once: in the visible table if it tripped the gate, here if
    it did not.
    """
    lines: list[str] = []
    for status, title in _DIFF_CONTEXT_SECTIONS:
        rows = [
            _diff_markdown_row(entry, links=links)
            for entry in report.entries
            if entry.status is status and entry.id not in gated
        ]
        if rows:
            lines.extend(_collapsed_section(rows, title=title, header=_DIFF_HEADER))
    return lines


def render_regressions_pr_comment(
    regressions: list[Regression],
    *,
    limit: int | None = _PR_COMMENT_ROW_CAP,
    links: SourceLinks | None = None,
    diff_report: DiffReport | None = None,
) -> str:
    """Render the `check` PR comment, showing `limit` rows before collapsing.

    This is the comment the Action posts, and the visible table is the set the
    gate acted on — so the body can never contradict the exit code the Action
    reports beside it. In baseline mode `diff_report` supplies the richer diff
    as collapsed context. Selecting the visible rows by `DiffStatus` instead is
    what let an exit-1 run post "_No risk regressions detected._", and an exit-0
    run post a visible regression row: the gate's `existing_above_threshold`
    fires on entries that are `UNCHANGED` by construction, and a `NEW` entry
    below `fail_new_above` trips no gate at all.

    It used to emit every row unbounded: riskratchet's own repo produced 345
    rows / ~49k characters, so a moderately larger repo crossed GitHub's 65,536
    limit and failed the Action instead of posting a report.
    """
    header = [
        "| Kind | Function | Before | After | Delta | Reason |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    lines = [
        PR_COMMENT_MARKER,
        "# riskratchet",
        "",
        _regressions_summary_line(regressions),
    ]
    if diff_report is not None:
        lines.append(_diff_context_line(diff_report))
        baseline = baseline_line(diff_report)
        if baseline:
            lines.append(f"_{baseline}_")
    lines.append("")
    displayed: list[Regression] = []
    if regressions:
        displayed = regressions if limit is None else regressions[:limit]
        lines.extend(header)
        lines.extend(_regression_markdown_row(reg, links=links) for reg in displayed)
    else:
        lines.append("_No risk regressions detected._")
    collapsed = regressions[len(displayed) :]
    if collapsed:
        lines.extend(
            _collapsed_section(
                [_regression_markdown_row(reg, links=links) for reg in collapsed],
                title="Lower-priority regressions",
                header=header,
            )
        )
    if diff_report is not None:
        lines.extend(
            _diff_context_sections(
                diff_report,
                gated=frozenset(reg.id for reg in regressions),
                links=links,
            )
        )
    return _fit_pr_comment("\n".join(lines) + "\n")


def render_diff_markdown(report: DiffReport, *, links: SourceLinks | None = None) -> str:
    lines = [
        "# riskratchet diff",
        "",
        _diff_summary_line(report),
        "",
        "| Status | Function | Before | After | Delta | Reason |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for entry in report.entries:
        lines.append(_diff_markdown_row(entry, links=links))
    return "\n".join(lines) + "\n"


def render_diff_pr_comment(report: DiffReport, *, links: SourceLinks | None = None) -> str:
    visible = [
        entry
        for entry in report.entries
        if entry.status
        in {
            DiffStatus.REGRESSED,
            DiffStatus.COMPONENT_REGRESSED,
            DiffStatus.AMBIGUOUS_RENAME,
            DiffStatus.NEW,
        }
    ]
    lines = [
        PR_COMMENT_MARKER,
        "# riskratchet",
        "",
        _diff_summary_line(report),
        "",
    ]
    if visible:
        lines.extend(_DIFF_HEADER)
        lines.extend(_diff_markdown_row(entry, links=links) for entry in visible)
    else:
        lines.append("_No risk regressions detected._")
    for status, title in (
        (DiffStatus.IMPROVED, "Improvements"),
        (DiffStatus.MOVED, "Moved functions"),
        (DiffStatus.REMOVED, "Removed functions"),
        (DiffStatus.UNCHANGED, "Unchanged functions"),
    ):
        entries = [entry for entry in report.entries if entry.status is status]
        if entries:
            lines.extend(
                _collapsed_section(
                    [_diff_markdown_row(entry, links=links) for entry in entries],
                    title=title,
                    header=_DIFF_HEADER,
                )
            )
    return _fit_pr_comment("\n".join(lines) + "\n")


def _markdown_row(
    fn: FunctionRisk,
    *,
    links: SourceLinks | None = None,
    include_group: bool = False,
) -> str:
    target = f"`{fn.id.as_target()}`"
    if links is not None:
        target = f"[{target}]({links.link_for(fn)})"
    cells = [
        severity(fn.score).value,
        f"{fn.score:.1f}",
        f"{fn.crap:.1f}",
        str(fn.complexity.cyclomatic),
        f"{round(fn.coverage.line_coverage * 100)}%",
        _branch_markdown(fn),
    ]
    if include_group:
        cells.append(fn.group or "ungrouped")
    cells.extend([target, f"{fn.span.start_line}-{fn.span.end_line}"])
    return "| " + " | ".join(cells) + " |"


def _regression_markdown_row(reg: Regression, *, links: SourceLinks | None = None) -> str:
    target = f"`{reg.id.as_target()}`"
    if links is not None and reg.current is not None:
        target = f"[{target}]({links.link_for(reg.current)})"
    cells = [
        reg.kind.value,
        target,
        _fmt_optional(reg.previous_score),
        f"{reg.current_score:.1f}",
        _fmt_optional(reg.delta, signed=True),
        reg.reason,
    ]
    return "| " + " | ".join(cells) + " |"


def _diff_markdown_row(entry: DiffEntry, *, links: SourceLinks | None = None) -> str:
    target = f"`{entry.id.as_target()}`"
    if links is not None and entry.current is not None:
        target = f"[{target}]({links.link_for(entry.current)})"
    cells = [
        entry.status.value,
        target,
        _fmt_optional(entry.previous_score),
        _fmt_optional(entry.current_score),
        _fmt_optional(entry.delta, signed=True),
        entry.reason,
    ]
    return "| " + " | ".join(cells) + " |"
