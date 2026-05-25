"""riskratchet: A maintainability ratchet for AI-assisted Python."""

from riskratchet._version import __version__
from riskratchet.baseline import (
    baseline_from_report,
    compare,
    load_baseline,
    save_baseline,
)
from riskratchet.engine import analyze
from riskratchet.models import (
    Baseline,
    BaselineEntry,
    ChurnStats,
    ComplexityStats,
    CoverageStats,
    FileStats,
    FunctionId,
    FunctionRisk,
    FunctionSpan,
    Regression,
    RegressionKind,
    RiskComponents,
    RiskReport,
    Severity,
)
from riskratchet.scoring import severity

__all__ = [
    "Baseline",
    "BaselineEntry",
    "ChurnStats",
    "ComplexityStats",
    "CoverageStats",
    "FileStats",
    "FunctionId",
    "FunctionRisk",
    "FunctionSpan",
    "Regression",
    "RegressionKind",
    "RiskComponents",
    "RiskReport",
    "Severity",
    "__version__",
    "analyze",
    "baseline_from_report",
    "compare",
    "load_baseline",
    "save_baseline",
    "severity",
]
