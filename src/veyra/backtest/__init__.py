"""Veyra backtesting / research engine (Phase 4).

A deterministic, chronological historical simulator. It answers the question:

    "If Veyra's current setup hypotheses had been applied to this historical
     data with these execution assumptions, what would have happened?"

It is a research/measurement tool, NOT an optimiser. It never claims a strategy
is profitable and respects the hard rule:

    PAST DATA -> DECISION
    FUTURE DATA -> OUTCOME ONLY
"""

from .engine import BacktestEngine
from .metrics import MetricsEngine, MetricsResult
from .models import (
    BacktestEvent,
    BacktestRun,
    BacktestTrade,
    ExecutionConfig,
    ExitReason,
    SetupOutcome,
    SetupRecord,
    SimulationResult,
    SplitConfig,
)
from .normalize import ScoreNormalizer
from .report import BacktestReporter
from .split import SplitConfig as _SplitConfig
from .split import Split, SplitIndices, chronological_split, walk_forward_windows
from .walk_forward import WalkForwardResult, WalkForwardRunner

__all__ = [
    "BacktestEngine",
    "BacktestEvent",
    "BacktestReporter",
    "BacktestRun",
    "BacktestTrade",
    "ExecutionConfig",
    "ExitReason",
    "MetricsEngine",
    "MetricsResult",
    "ScoreNormalizer",
    "SetupOutcome",
    "SetupRecord",
    "SimulationResult",
    "Split",
    "SplitConfig",
    "SplitIndices",
    "WalkForwardResult",
    "WalkForwardRunner",
    "chronological_split",
    "walk_forward_windows",
]