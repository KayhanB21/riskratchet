"""Core dataclasses and enums shared across the package.

All types here are pure data; no I/O, no business logic. Keeping them in one
file makes the contract between modules easy to grep and audit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RegressionKind(str, Enum):
    NEW_ABOVE_THRESHOLD = "new_above_threshold"
    REGRESSED = "regressed"


@dataclass(frozen=True, slots=True)
class FunctionId:
    """Stable identifier for a function across runs.

    `path` is the project-relative POSIX path. `qualname` is the dotted name
    relative to the module (e.g. "Foo.bar" for a method, "outer.inner" for a
    nested function). The combination must uniquely identify a function in a
    single project snapshot.
    """

    path: str
    qualname: str

    def as_target(self) -> str:
        return f"{self.path}::{self.qualname}"


@dataclass(frozen=True, slots=True)
class FunctionSpan:
    start_line: int
    end_line: int

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1


@dataclass(frozen=True, slots=True)
class CoverageStats:
    """Line and branch coverage for a single function.

    `line_coverage` and `branch_coverage` are fractions in [0, 1]. When branch
    coverage was not measured for the file, `branch_coverage` is None and the
    branch-gap component will not contribute to risk.
    """

    line_coverage: float
    branch_coverage: float | None
    missing_lines: tuple[int, ...] = ()
    missing_branches: tuple[tuple[int, int], ...] = ()

    @classmethod
    def uncovered(cls) -> CoverageStats:
        return cls(line_coverage=0.0, branch_coverage=None)


@dataclass(frozen=True, slots=True)
class ComplexityStats:
    cyclomatic: int


@dataclass(frozen=True, slots=True)
class ChurnStats:
    commits: int


@dataclass(frozen=True, slots=True)
class FileStats:
    path: str
    total_lines: int
    function_count: int


@dataclass(frozen=True, slots=True)
class RiskComponents:
    coverage_gap: float
    structural_complexity: float
    branch_gap: float
    churn: float
    public_surface: float
    sprawl: float


@dataclass(frozen=True, slots=True)
class FunctionRisk:
    id: FunctionId
    span: FunctionSpan
    is_public: bool
    complexity: ComplexityStats
    coverage: CoverageStats
    churn: ChurnStats
    file_stats: FileStats
    components: RiskComponents
    score: float
    crap: float


@dataclass(frozen=True, slots=True)
class RiskReport:
    functions: tuple[FunctionRisk, ...]
    files: tuple[FileStats, ...]

    def by_id(self) -> dict[FunctionId, FunctionRisk]:
        return {fn.id: fn for fn in self.functions}

    def find(self, target: str) -> FunctionRisk | None:
        for fn in self.functions:
            if fn.id.as_target() == target:
                return fn
        return None


@dataclass(frozen=True, slots=True)
class BaselineEntry:
    id: FunctionId
    score: float
    components: RiskComponents


@dataclass
class Baseline:
    """Persisted snapshot of per-function risk scores.

    Not frozen because the entries dict is keyed by FunctionId for fast lookup
    after deserialization; baselines are read-then-compared, never mutated.
    """

    version: str
    entries: dict[FunctionId, BaselineEntry] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Regression:
    id: FunctionId
    kind: RegressionKind
    current_score: float
    previous_score: float | None
    delta: float | None
    reason: str
    current: FunctionRisk | None = None
