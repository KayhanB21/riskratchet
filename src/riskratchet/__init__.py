"""riskratchet: A maintainability ratchet for AI-assisted Python."""

from riskratchet.baseline import (
    Baseline,
    BaselineEntry,
    baseline_from_report,
    compare,
    load_baseline,
    save_baseline,
)
from riskratchet.engine import analyze
from riskratchet.models import (
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

__version__ = "0.1.0"
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
    "analyze",
    "baseline_from_report",
    "compare",
    "load_baseline",
    "save_baseline",
    "severity",
]
