"""Human-readable text renderers: Rich tables, summary lines, and the
verbose function explanation used by `riskratchet explain`."""

from __future__ import annotations

from collections.abc import Iterable
from io import StringIO

from rich.console import Console
from rich.table import Table

from riskratchet.models import (
    DiffReport,
    FunctionRisk,
    Regression,
    RiskReport,
    Severity,
)
from riskratchet.reporting.summary import (
    SourceLinks,
    _branch_cell,
    _branch_pct,
    _diff_summary,
    _fmt_optional,
    _group_summary_lines,
    _regressions_summary,
    _remediation,
    _severity_summary_line,
    _sorted_by_risk,
    _summary_line,
    _summary_payload,
)
from riskratchet.scoring import severity

_SEVERITY_STYLE: dict[Severity, str] = {
    Severity.LOW: "green",
    Severity.MEDIUM: "yellow",
    Severity.HIGH: "red",
    Severity.CRITICAL: "bold red",
}


def _make_buffered_console() -> tuple[StringIO, Console]:
    """Deterministic Console wired to a StringIO buffer.

    All three Rich-rendered tables (`render_report_table`,
    `render_regressions_table`, `render_diff_table`) share these
    exact settings: no terminal, no color, fixed 120-char width.
    The settings make `Console.print()` output byte-stable across
    environments.
    """
    buf = StringIO()
    return buf, Console(file=buf, force_terminal=False, color_system=None, width=120)


def render_report_table(
    report: RiskReport,
    *,
    limit: int | None = 20,
    include_summary: bool = True,
    links: SourceLinks | None = None,
) -> str:
    sorted_fns = _sorted_by_risk(report.functions)
    displayed = sorted_fns if limit is None else sorted_fns[:limit]

    buf, console = _make_buffered_console()
    table = Table(title="riskratchet scan", show_header=True, header_style="bold")
    table.add_column("Sev")
    table.add_column("Score", justify="right")
    table.add_column("CRAP", justify="right")
    table.add_column("CC", justify="right")
    table.add_column("LCov %", justify="right")
    table.add_column("BCov %", justify="right")
    table.add_column("Function")
    table.add_column("Lines", justify="right")

    for fn in displayed:
        sev = severity(fn.score)
        table.add_row(
            f"[{_SEVERITY_STYLE[sev]}]{sev.value}[/]",
            f"{fn.score:.1f}",
            f"{fn.crap:.1f}",
            str(fn.complexity.cyclomatic),
            f"{fn.coverage.line_coverage * 100:.0f}",
            _branch_cell(fn),
            fn.id.as_target(),
            f"{fn.span.start_line}-{fn.span.end_line}",
        )
    console.print(table)
    if limit is not None and len(sorted_fns) > limit:
        console.print(f"... {len(sorted_fns) - limit} more functions hidden (use --limit to show more)")
    if include_summary:
        console.print(_summary_line(report))
        if report.coverage_status == "missing":
            console.print("Coverage: missing (all functions are treated as uncovered).")
    if links is not None:
        buf.write(_table_source_footer((fn.id.as_target(), links.link_for(fn)) for fn in displayed))
    return buf.getvalue()


def render_report_summary_text(report: RiskReport) -> str:
    summary = _summary_payload(report)
    lines = [
        (
            "scan "
            f"functions={summary['total_functions']} "
            f"analyzed={summary['analyzed_functions']} "
            f"emitted={summary['emitted_functions']} "
            f"files={summary['total_files']} "
            f"coverage={summary['coverage_status']} "
            f"suppressed={summary['suppressed_functions']} "
            f"skipped_missing_coverage={summary['skipped_missing_coverage']}"
        ),
        _severity_summary_line(summary["by_severity"]),
    ]
    lines.extend(_group_summary_lines(summary.get("groups", {})))
    return "\n".join(lines) + "\n"


def render_regressions_table(regressions: list[Regression], *, links: SourceLinks | None = None) -> str:
    buf, console = _make_buffered_console()
    if not regressions:
        console.print("[green]No risk regressions detected.[/]")
        return buf.getvalue()
    table = Table(title="riskratchet regressions", show_header=True, header_style="bold red")
    table.add_column("Kind")
    table.add_column("Function", no_wrap=True)
    table.add_column("Before", justify="right")
    table.add_column("After", justify="right")
    table.add_column("Delta", justify="right")
    table.add_column("Reason")
    for reg in regressions:
        table.add_row(
            reg.kind.value,
            reg.id.as_target(),
            _fmt_optional(reg.previous_score),
            f"{reg.current_score:.1f}",
            _fmt_optional(reg.delta, signed=True),
            reg.reason,
        )
    console.print(table)
    if links is not None:
        buf.write(
            _table_source_footer((reg.id.as_target(), _regression_link(reg, links)) for reg in regressions)
        )
    return buf.getvalue()


def render_regressions_summary_text(
    regressions: list[Regression],
    *,
    diff_report: DiffReport | None = None,
) -> str:
    summary = _regressions_summary(regressions, diff_report=diff_report)
    by_kind = summary["by_kind"]
    lines = [
        (
            "check "
            f"regressions={summary['regressions']} "
            f"new_above_threshold={by_kind['new_above_threshold']} "
            f"regressed={by_kind['regressed']} "
            f"existing_above_threshold={by_kind['existing_above_threshold']} "
            f"component_regressed={by_kind['component_regressed']} "
            f"above_threshold={by_kind['above_threshold']}"
        )
    ]
    if "diff" in summary:
        diff_summary = summary["diff"]
        lines.append(
            "diff "
            f"regressed={diff_summary['regressed']} "
            f"component_regressed={diff_summary['component_regressed']} "
            f"improved={diff_summary['improved']} "
            f"new={diff_summary['new']} "
            f"ambiguous_rename={diff_summary['ambiguous_rename']} "
            f"removed={diff_summary['removed']} "
            f"moved={diff_summary['moved']} "
            f"unchanged={diff_summary['unchanged']}"
        )
    lines.extend(_group_summary_lines(summary.get("groups", {})))
    return "\n".join(lines) + "\n"


def render_diff_table(report: DiffReport, *, links: SourceLinks | None = None) -> str:
    buf, console = _make_buffered_console()
    table = Table(title="riskratchet diff", show_header=True, header_style="bold")
    table.add_column("Status")
    table.add_column("Function", no_wrap=True)
    table.add_column("Before", justify="right")
    table.add_column("After", justify="right")
    table.add_column("Delta", justify="right")
    table.add_column("Reason")
    for entry in report.entries:
        table.add_row(
            entry.status.value,
            entry.id.as_target(),
            _fmt_optional(entry.previous_score),
            _fmt_optional(entry.current_score),
            _fmt_optional(entry.delta, signed=True),
            entry.reason,
        )
    console.print(table)
    if links is not None:
        rows: list[tuple[str, str]] = []
        for entry in report.entries:
            fn = entry.current
            if fn is None:
                continue
            rows.append((entry.id.as_target(), links.link_for(fn)))
        buf.write(_table_source_footer(rows))
    return buf.getvalue()


def render_diff_summary_text(report: DiffReport) -> str:
    summary = _diff_summary(report)
    lines = [
        (
            "diff "
            f"regressed={summary['regressed']} "
            f"component_regressed={summary['component_regressed']} "
            f"improved={summary['improved']} "
            f"new={summary['new']} "
            f"ambiguous_rename={summary['ambiguous_rename']} "
            f"removed={summary['removed']} "
            f"moved={summary['moved']} "
            f"unchanged={summary['unchanged']}"
        )
    ]
    lines.extend(_group_summary_lines(summary.get("groups", {})))
    return "\n".join(lines) + "\n"


def _regression_link(reg: Regression, links: SourceLinks) -> str:
    """Choose the best span for a regression's source URL.

    `Regression.current` is the post-state FunctionRisk; when missing
    (entries that only exist in baseline), fall back to a 1-line span
    via `Regression.id.path` so the URL still points somewhere useful.
    """
    if reg.current is not None:
        return links.link_for(reg.current)
    return links.link_for_span(reg.id.path, 1, 1)


def _table_source_footer(rows: Iterable[tuple[str, str]]) -> str:
    """Build the `Source:` footer block under a Rich table.

    Rendered with direct string writes (not Rich) so the addition
    leaves byte-stable snapshots for callers that don't pass `links`.
    Duplicates by qualname are dropped so a function listed twice in
    the table (e.g. as both `current` and `previous`) gets one URL.
    """
    seen: list[tuple[str, str]] = []
    seen_targets: set[str] = set()
    for qualname, url in rows:
        if qualname in seen_targets:
            continue
        seen.append((qualname, url))
        seen_targets.add(qualname)
    if not seen:
        return ""
    out = ["", "Source:"]
    for qualname, url in seen:
        out.append(f"  {qualname:<40} {url}")
    out.append("")
    return "\n".join(out)


def render_function_explanation(fn: FunctionRisk) -> str:
    """Verbose, human-readable explanation for `explain` command."""
    sev = severity(fn.score)
    lines = [
        f"{fn.id.as_target()}",
        f"  severity     : {sev.value}",
        f"  score        : {fn.score:.1f}",
        f"  crap         : {fn.crap:.1f}",
        f"  complexity   : CC={fn.complexity.cyclomatic}",
        f"  coverage     : line={fn.coverage.line_coverage * 100:.0f}%, "
        f"branch={_branch_pct(fn.coverage.branch_coverage)}",
        f"  churn        : {fn.churn.commits} commits in window",
        f"  public       : {fn.is_public}",
        f"  lines        : {fn.span.start_line}-{fn.span.end_line} "
        f"(function {fn.span.line_count} lines, file {fn.file_stats.total_lines})",
        "  components   :",
        f"    coverage_gap          {fn.components.coverage_gap:.1f}",
        f"    structural_complexity {fn.components.structural_complexity:.1f}",
        f"    branch_gap            {fn.components.branch_gap:.1f}",
        f"    churn                 {fn.components.churn:.1f}",
        f"    public_surface        {fn.components.public_surface:.1f}",
        f"    sprawl                {fn.components.sprawl:.1f}",
        "",
        _remediation(fn),
    ]
    return "\n".join(lines) + "\n"
