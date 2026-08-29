"""GitHub Actions workflow annotation lines — `--format github`."""

from __future__ import annotations

from riskratchet.models import (
    DiffReport,
    DiffStatus,
    FunctionRisk,
    Regression,
    RiskReport,
)
from riskratchet.reporting.summary import _sorted_by_risk
from riskratchet.scoring import severity


def render_report_github(report: RiskReport, *, min_score: float = 25.0) -> str:
    lines = [_github_annotation(fn) for fn in _sorted_by_risk(report.functions) if fn.score >= min_score]
    return "\n".join(lines) + ("\n" if lines else "")


def render_regressions_github(regressions: list[Regression]) -> str:
    lines = []
    for reg in regressions:
        if reg.current is not None:
            lines.append(_github_annotation(reg.current, message=reg.reason))
        else:
            lines.append(
                f"::warning file={_escape_github_property(reg.id.path)}::{_escape_github_data(reg.reason)}"
            )
    return "\n".join(lines) + ("\n" if lines else "")


def render_diff_github(report: DiffReport) -> str:
    lines = []
    for entry in report.entries:
        if entry.status not in {
            DiffStatus.REGRESSED,
            DiffStatus.COMPONENT_REGRESSED,
            DiffStatus.AMBIGUOUS_RENAME,
            DiffStatus.NEW,
        }:
            continue
        if entry.current is not None:
            lines.append(_github_annotation(entry.current, message=entry.reason))
    return "\n".join(lines) + ("\n" if lines else "")


def _github_annotation(fn: FunctionRisk, *, message: str | None = None) -> str:
    text = message or (
        f"{fn.id.as_target()} has {severity(fn.score).value} risk: "
        f"score {fn.score:.1f}, CRAP {fn.crap:.1f}, complexity {fn.complexity.cyclomatic}"
    )
    return (
        f"::warning file={_escape_github_property(fn.id.path)}"
        f",line={fn.span.start_line},endLine={fn.span.end_line}"
        f"::{_escape_github_data(text)}"
    )


def _escape_github_data(value: str) -> str:
    """Escape the message half of a workflow command.

    The runner's `unescapeData` reverses exactly these three and nothing else,
    so escaping `:` here — as this did — is one-way: every annotation
    riskratchet has written rendered `src/m.py%3A%3Ahuge` rather than
    `src/m.py::huge`, making the identifier uncopyable.
    """
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_github_property(value: str) -> str:
    """Escape a `key=value` property of a workflow command.

    `unescapeProperty` reverses these five. `:` and `,` terminate the property
    list, so a path containing either broke parsing and the runner dropped the
    annotation entirely — the half that was escaping nothing at all.
    """
    return _escape_github_data(value).replace(":", "%3A").replace(",", "%2C")
