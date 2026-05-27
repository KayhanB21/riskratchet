"""Markdown and PR-comment renderers.

`SourceLinks` lives here because the markdown/PR renderers are its
only consumers. It is re-exported from `riskratchet.reporting` for
backwards compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass

from riskratchet.models import (
    DiffEntry,
    DiffReport,
    DiffStatus,
    FunctionRisk,
    Regression,
    RiskReport,
    Severity,
)
from riskratchet.reporting.summary import (
    PR_COMMENT_MARKER,
    _branch_markdown,
    _diff_summary_line,
    _fmt_optional,
    _sorted_by_risk,
    _summary_line,
)
from riskratchet.scoring import severity


@dataclass(frozen=True, slots=True)
class SourceLinks:
    repo_url: str
    commit_ref: str

    def link_for(self, fn: FunctionRisk) -> str:
        return (
            f"{self.repo_url.rstrip('/')}/blob/{self.commit_ref}/"
            f"{fn.id.path}#L{fn.span.start_line}-L{fn.span.end_line}"
        )


def render_report_markdown(
    report: RiskReport,
    *,
    limit: int | None = 20,
    links: SourceLinks | None = None,
) -> str:
    sorted_fns = _sorted_by_risk(report.functions)
    displayed = sorted_fns if limit is None else sorted_fns[:limit]
    lines = [
        "# riskratchet report",
        "",
        f"**Functions analyzed:** {len(report.functions)}",
        f"**Files analyzed:** {len(report.files)}",
        f"**Coverage:** {report.coverage_status}",
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
        lines.extend(["", f"<details><summary>Lower-priority findings ({len(collapsed)})</summary>", ""])
        lines.extend(
            [
                "| Severity | Score | CRAP | CC | LCov | BCov | Group | Function | Lines |",
                "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: |",
            ]
        )
        lines.extend(_markdown_row(fn, links=links, include_group=True) for fn in collapsed[:20])
        if len(collapsed) > 20:
            lines.append(f"_... {len(collapsed) - 20} more hidden._")
        lines.extend(["", "</details>"])
    return "\n".join(lines) + "\n"


def render_regressions_markdown(regressions: list[Regression], *, links: SourceLinks | None = None) -> str:
    if not regressions:
        return "_No risk regressions detected._\n"
    lines = [
        "# riskratchet regressions",
        "",
        "| Kind | Function | Before | After | Delta | Reason |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for reg in regressions:
        lines.append(_regression_markdown_row(reg, links=links))
    return "\n".join(lines) + "\n"


def render_regressions_pr_comment(
    regressions: list[Regression],
    *,
    links: SourceLinks | None = None,
) -> str:
    lines = [
        PR_COMMENT_MARKER,
        "# riskratchet",
        "",
    ]
    if not regressions:
        lines.append("_No risk regressions detected._")
        return "\n".join(lines) + "\n"
    lines.extend(
        [
            "| Kind | Function | Before | After | Delta | Reason |",
            "| --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    lines.extend(_regression_markdown_row(reg, links=links) for reg in regressions)
    return "\n".join(lines) + "\n"


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
        lines.extend(
            [
                "| Status | Function | Before | After | Delta | Reason |",
                "| --- | --- | ---: | ---: | ---: | --- |",
            ]
        )
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
            lines.extend(["", f"<details><summary>{title} ({len(entries)})</summary>", ""])
            lines.extend(
                [
                    "| Status | Function | Before | After | Delta | Reason |",
                    "| --- | --- | ---: | ---: | ---: | --- |",
                ]
            )
            lines.extend(_diff_markdown_row(entry, links=links) for entry in entries[:20])
            if len(entries) > 20:
                lines.append(f"_... {len(entries) - 20} more hidden._")
            lines.extend(["", "</details>"])
    return "\n".join(lines) + "\n"


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
