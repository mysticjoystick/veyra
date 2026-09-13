"""Backtest research domain models.

These are immutable-ish, machine-testable records of a historical simulation.
They deliberately do NOT live in the live setup repository: a backtest run must
be reproducible without mutating live setup state.

The models capture execution assumptions, lifecycle outcomes (setups that never
trigger are NOT losing trades), and per-trade results with full auditability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------


class ExitReason(str, Enum):
    """Why a triggered position was closed."""

    STOP = "STOP"
    TARGET = "TARGET"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    END_OF_DATA = "END_OF_DATA"


class SetupOutcome(str, Enum):
    """How a setup resolved WITHOUT necessarily producing a trade.

    A setup that never triggers is tracked here, NOT converted into a losing
    trade. This preserves the count of candidates vs trades.
    """

    DETECTED_ONLY = "DETECTED_ONLY"      # never reached QUALIFIED / never triggered
    QUALIFIED_NO_TRADE = "QUALIFIED_NO_TRADE"  # qualified but no position result
    COMPLETED = "COMPLETED"              # produced a triggered trade that closed
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"


# ---------------------------------------------------------------------------
# Immutable execution / run configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutionConfig:
    """Deterministic execution assumptions for a backtest run."""

    entry_fee_pct: float = 0.001
    exit_fee_pct: float = 0.001
    slippage_pct: float = 0.0
    spread_pct: float = 0.0
    entry_policy: str = "next_open"
    ambiguous_candle_policy: str = "stop_first"
    overlap_policy: str = "ALLOW_OVERLAP"
    position_max_bars: int = 60

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: dict) -> "ExecutionConfig":
        known = {
            k: v for k, v in data.items() if k in cls.__dataclass_fields__
        }
        return cls(**known)


@dataclass(frozen=True)
class SplitConfig:
    """Chronological train / validation / test split (fractions, order-preserving)."""

    train: float = 0.6
    validation: float = 0.2
    test: float = 0.2

    def to_dict(self) -> dict:
        return {"train": self.train, "validation": self.validation, "test": self.test}


# ---------------------------------------------------------------------------
# BacktestRun / BacktestTrade / BacktestEvent
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BacktestRun:
    """Top-level metadata for one historical simulation."""

    run_key: str                      # deterministic fingerprint of config+data
    symbol: str
    timeframe: str
    start_time: int
    end_time: int
    candle_count: int
    config_snapshot: Dict[str, object]   # serialised settings used
    execution: ExecutionConfig
    split: SplitConfig
    strategy_version: str
    engine_version: str
    dataset_hash: str
    created_by: str = "backtest"
    # Coverage audit (Phase 6/§14): every bar is analysed, but only bars with a
    # live setup produce records. These two counters make the "quiet" majority
    # visible so downstream coverage is honest. Additive only — no semantic change.
    analyzed_bars: int = -1   # bars actually run through the engine (post-warmup)
    bars_with_setup: int = -1 # of those, how many had >= 1 live setup

    def to_dict(self) -> dict:
        return {
            "run_key": self.run_key,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "candle_count": self.candle_count,
            "config_snapshot": self.config_snapshot,
            "execution": self.execution.to_dict(),
            "split": self.split.to_dict(),
            "strategy_version": self.strategy_version,
            "engine_version": self.engine_version,
            "dataset_hash": self.dataset_hash,
            "created_by": self.created_by,
            "analyzed_bars": self.analyzed_bars,
            "bars_with_setup": self.bars_with_setup,
        }


@dataclass
class BacktestTrade:
    """A triggered setup that produced (or would have produced) a position."""

    trade_id: str
    setup_key: str
    symbol: str
    timeframe: str
    setup_type: str
    regime: str
    score: int
    score_normalized: int       # 0-100 normalised (see normalize.py)
    side: str
    detection_ts: int
    qualification_ts: int
    entry_ts: int
    entry_price: float
    invalidation: str
    exit_ts: int
    exit_price: float
    exit_reason: str
    gross_return: float         # fractional return (0.05 = +5%)
    fees: float                 # total fees paid (fraction of notional)
    slippage: float             # total slippage impact (fraction)
    net_return: float           # gross - fees - slippage
    holding_bars: int
    max_favorable_excursion: float   # best price reached mid-hold (raw price move)
    max_adverse_excursion: float     # worst price reached mid-hold (raw price move)
    overlap: bool = False

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass(frozen=True)
class BacktestEvent:
    """A single reproducible event in a backtest (append-only)."""

    run_key: str
    timestamp: int
    event_type: str            # SETUP_DETECTED / SETUP_QUALIFIED / ENTRY / ...
    symbol: str
    timeframe: str
    setup_key: Optional[str] = None
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "run_key": self.run_key,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "setup_key": self.setup_key,
            "detail": self.detail,
        }


@dataclass
class SetupRecord:
    """Full lifecycle record for one detected setup (whether or not it traded)."""

    key: str
    symbol: str
    timeframe: str
    detection_ts: int
    setup_type: str
    regime: str
    score: int
    score_normalized: int
    side: str
    interest_area_low: float
    interest_area_high: float
    invalidation: str
    final_state: str                   # terminal SetupState or DETECTED
    outcome: str                       # SetupOutcome
    trade_id: Optional[str] = None

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class SimulationResult:
    """Everything produced by one replay run."""

    run: BacktestRun
    setups: List[SetupRecord] = field(default_factory=list)
    trades: List[BacktestTrade] = field(default_factory=list)
    events: List[BacktestEvent] = field(default_factory=list)
    split_indices: Optional[object] = None
    # Coverage audit histogram: bins describing how many live setups were active
    # per analysed bar (index 0 = "no live setup / WAIT bars").
    coverage_by_live: List[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run": self.run.to_dict(),
            "setups": [s.to_dict() for s in self.setups],
            "trades": [t.to_dict() for t in self.trades],
            "events": [e.to_dict() for e in self.events],
            "coverage_by_live": self.coverage_by_live,
        }

    def coverage(self) -> dict:
        """Honest coverage over the analysed timeline.

        coverage_fraction = bars with >= 1 live setup / analysed bars. This is
        the fraction of time the strategy is actually 'in the market' looking
        for an opportunity. With 0 analysed bars the ratio is 0.0.

        Wait bars (no live setup) = analysed_bars - bars_with_setup, surfaced
        explicitly so downstream consumers never silently drop the quiet bars.
        """
        analysed = self.run.analyzed_bars if self.run.analyzed_bars >= 0 else 0
        covered = self.run.bars_with_setup if self.run.bars_with_setup >= 0 else 0
        if analysed <= 0:
            return {
                "analyzed_bars": 0,
                "bars_with_setup": 0,
                "wait_bars": 0,
                "coverage_fraction": 0.0,
            }
        return {
            "analyzed_bars": analysed,
            "bars_with_setup": covered,
            "wait_bars": max(0, analysed - covered),
            "coverage_fraction": covered / analysed,
        }