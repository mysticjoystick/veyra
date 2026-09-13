"""Phase 5 validation orchestrator.

Produce the historical / breakdown / period evidence for a single real dataset
using the frozen Phase 4 baseline (see ``frozen_config``). All engine runs use
progressive single-pass mode -- the slice path is O(N^2) and intractable at the
full 4H dataset size.

A full-period backtest is run exactly ONCE per dataset:
  * overall metrics (MetricsEngine over all trades/setups)
  * breakdowns       (by setup type / regime / timeframe / fixed score buckets)
  * period analysis   (chronological IN / VALIDATION / OOS split, filtering the
                       same single run -- no re-running, so periods share the
                       exact same historical engine state)

Out-of-sample integrity is preserved by construction: the engine only ever sees
information up to each bar when a decision is made, so slicing the *results* into
periods cannot leak future info.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Dict, List, Optional

import pandas as pd

from ..backtest import MetricsEngine, MetricsResult, chronological_split
from ..config import Settings
from ..data.candle_store import CandleStore
from ..phase5.frozen_config import build_baseline_engine

# Fixed, pre-registered score buckets (normalized score = 0..100).
# Format used by MetricsEngine: (label, low, high).
FIXED_SCORE_BUCKETS: List[tuple] = [
    ("0-19", 0, 19),
    ("20-39", 20, 39),
    ("40-59", 40, 59),
    ("60-79", 60, 79),
    ("80-100", 80, 100),
]

_PERIODS = [("IN", "training"), ("VALIDATION", "validation"), ("OOS", "test")]


@dataclass
class PeriodResult:
    label: str
    start_ts: int
    end_ts: int
    index: range
    counts: Dict[str, int]
    metrics: MetricsResult


@dataclass
class DatasetValidation:
    symbol: str
    timeframe: str
    candle_count: int
    start_ts: int
    end_ts: int
    strategy_version: str
    engine_version: str
    overall: MetricsResult
    breakdown: MetricsResult
    periods: List[PeriodResult] = field(default_factory=list)
    # Raw objects for downstream report detail; excluded from serialization.
    trades: List = field(default_factory=list)
    setups: List = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "candle_count": self.candle_count,
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "strategy_version": self.strategy_version,
            "engine_version": self.engine_version,
            "overall": _serialize(self.overall),
            "breakdown": _serialize(self.breakdown),
            "periods": [
                {
                    "label": p.label,
                    "start_ts": p.start_ts,
                    "end_ts": p.end_ts,
                    "index": (p.index.start, p.index.stop),
                    "counts": p.counts,
                    "metrics": _serialize(p.metrics),
                }
                for p in self.periods
            ],
        }


def _serialize(obj) -> dict:
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if is_dataclass(obj):
        return asdict(obj)
    raise TypeError(f"cannot serialize {type(obj).__name__}")


def _engine_version_str(ev) -> str:
    """Render an EngineVersion (dataclass) as a compact provenance string."""
    if ev is None:
        return ""
    if hasattr(ev, "model_dump"):
        return ";".join(f"{k}={v}" for k, v in ev.model_dump().items())
    if hasattr(ev, "__dict__"):
        return ";".join(f"{k}={v}" for k, v in ev.__dict__.items())
    return str(ev)


class Phase5Validator:
    """Run frozen-baseline validation over real datasets."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        store: Optional[CandleStore] = None,
    ) -> None:
        self._settings = settings or Settings()
        self._store = store or CandleStore(self._settings)
        self._metrics = MetricsEngine()

    def datasets(self) -> List[Dict[str, str]]:
        syms = self._settings.phase5_symbols or ["BTC/USDT", "ETH/USDT"]
        tfs = self._settings.phase5_timeframes or ["1D", "4H"]
        return [
            {"symbol": sym, "timeframe": tf} for sym in syms for tf in tfs
        ]

    def validate(
        self,
        symbol: str,
        timeframe: str,
        df: Optional[pd.DataFrame] = None,
    ) -> DatasetValidation:
        df = (
            self._store.load(symbol, timeframe)
            if df is None
            else df.sort_values("open_time").reset_index(drop=True)
        )

        engine = build_baseline_engine(self._settings, progressive=True)
        run = engine.run(symbol, timeframe, df.copy(), run_key=f"p5|{symbol}|{timeframe}")

        overall = self._metrics.compute(
            run.trades, run.setups, len(df), score_buckets=FIXED_SCORE_BUCKETS
        )
        breakdown = self._metrics.compute(
            run.trades, run.setups, len(df), score_buckets=FIXED_SCORE_BUCKETS
        )
        periods = self._periods(run, df)

        return DatasetValidation(
            symbol=symbol,
            timeframe=timeframe,
            candle_count=len(df),
            start_ts=int(df["open_time"].iloc[0]),
            end_ts=int(df["open_time"].iloc[-1]),
            strategy_version=run.run.strategy_version or "",
            engine_version=_engine_version_str(run.run.engine_version),
            overall=overall,
            breakdown=breakdown,
            periods=periods,
            trades=run.trades,
            setups=run.setups,
        )

    def _periods(self, run, df: pd.DataFrame) -> List[PeriodResult]:
        split = chronological_split(len(df))
        times = df["open_time"].to_numpy()

        periods: List[PeriodResult] = []
        for label, attr in _PERIODS:
            idx = getattr(split, attr)
            if idx is None or idx.end <= idx.start:
                continue
            ts_range_start = int(times[idx.start])
            ts_range_end = int(times[idx.end - 1])
            trades = [
                t
                for t in run.trades
                if int(t.entry_ts) >= ts_range_start and int(t.entry_ts) <= ts_range_end
            ]
            setups = [
                s
                for s in run.setups
                if int(s.detection_ts) >= ts_range_start
                and int(s.detection_ts) <= ts_range_end
            ]
            metrics = self._metrics.compute(trades, setups, len(df), score_buckets=FIXED_SCORE_BUCKETS)
            periods.append(
                PeriodResult(
                    label=label,
                    start_ts=ts_range_start,
                    end_ts=ts_range_end,
                    index=range(idx.start, idx.end),
                    counts={
                        "trades": len(trades),
                        "setups": len(setups),
                        "completed": sum(1 for s in setups if s.outcome == "COMPLETED"),
                    },
                    metrics=metrics,
                )
            )
        return periods