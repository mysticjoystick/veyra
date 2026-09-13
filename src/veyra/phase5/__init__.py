"""Phase 5: frozen config, real-data validation, calibration and reports.

This package implements the Phase 5 measurement/falsification workflow. It
never optimises parameters, claims profitability, or treats synthetic data as
evidence.
"""

from .frozen_config import (
    PHASE5_BASELINE_VERSION,
    FROZEN_EXECUTION,
    build_baseline_engine,
    snapshot_settings,
)
from .validate import (
    FIXED_SCORE_BUCKETS,
    DatasetValidation,
    PeriodResult,
    Phase5Validator,
)
from .calibration import (
    BucketCalibration,
    CalibrationEngine,
    CalibrationResult,
)
from .gate import (
    Build,
    Evidence,
    GateResult,
    GateStatus,
    Phase5Gates,
    Phase5ReportCard,
    Verdict,
)
from .report import (
    QUESTIONS,
    Phase5Report,
    Phase5Reporter,
    Phase5ReportSuite,
    ReportInputs,
)
from .robustness import (
    RobustnessEngine,
    RobustnessResult,
    RobustnessRow,
)
from .workflow import (
    DEFAULT_DATASETS,
    DatasetOutcome,
    Phase5Workflow,
)

__all__ = [
    "PHASE5_BASELINE_VERSION",
    "FROZEN_EXECUTION",
    "build_baseline_engine",
    "snapshot_settings",
    "FIXED_SCORE_BUCKETS",
    "DatasetValidation",
    "PeriodResult",
    "Phase5Validator",
    "BucketCalibration",
    "CalibrationEngine",
    "CalibrationResult",
    "Build",
    "Evidence",
    "GateResult",
    "GateStatus",
    "Phase5Gates",
    "Phase5ReportCard",
    "Verdict",
    "QUESTIONS",
    "Phase5Report",
    "Phase5Reporter",
    "Phase5ReportSuite",
    "ReportInputs",
    "RobustnessEngine",
    "RobustnessResult",
    "RobustnessRow",
    "DEFAULT_DATASETS",
    "DatasetOutcome",
    "Phase5Workflow",
]