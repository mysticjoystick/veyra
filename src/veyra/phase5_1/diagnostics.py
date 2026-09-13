"""Phase 5.1 strategy diagnosis — core analysis built on frozen Phase 5 outputs.

RE-USES the frozen Phase 5 validation infrastructure. The baseline strategy and
the real datasets are treated as immutable: nothing here re-optimises, retunes,
or touches the OOS/test data selection.

A single frozen-baseline engine run per dataset yields per-trade and per-setup
records; this module slices those records by setup type, score bucket, market
regime, asset, timeframe, chronological period, and execution mechanics. Every
metric pair is accompanied by its sample size so conclusions can be evaluated
honestly (no cherry-picking, no bare percentages).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd

from ..backtest import BacktestEngine, MetricsEngine, BacktestTrade, SetupRecord
from ..config import Settings, get_settings
from ..data.candle_store import CandleStore
from ..phase5.frozen_config import PHASE5_BASELINE_VERSION, build_baseline_engine

SETUP_TYPES = [
    "TREND_CONTINUATION",
    "PULLBACK",
    "BREAKOUT",
    "BREAKOUT_RETEST",
    "RANGE_REJECTION",
]

CONFIGURED_REGIMES = ["bull", "bear", "ranging", "high_volatility"]

TIME_FRAMES = ["4H", "1D"]

# Fixed score buckets, defined independently of results (mirror Phase 5).
SCORE_BUCKETS = ["0-19", "20-39", "40-59", "60-79", "80-100"]


@dataclass
class SliceStats:
    """Aggregate outcome stats for a slice's completed trades."""

    label: str
    n_trades: int
    n_setups: int  # detected or qualified, caller-specific
    wins: int
    losses: int
    expirations: int
    avg_return: float
    expectancy: Optional[float]
    profit_factor: Optional[float]
    win_rate: Optional[float]
    avg_holding_bars: Optional[float]
    fees: float
    slippage: float
    gross_to_net_gap: float

    def as_row(self) -> dict:
        return {
            "label": self.label,
            "trades": self.n_trades,
            "setups": self.n_setups,
            "wins": self.wins,
            "losses": self.losses,
            "expirations": self.expirations,
            "avg_return": self.avg_return,
            "expectancy": self.expectancy,
            "profit_factor": self.profit_factor,
            "win_rate": self.win_rate,
            "avg_holding_bars": self.avg_holding_bars,
            "fees": self.fees,
            "slippage": self.slippage,
            "cost_gap": self.gross_to_net_gap,
        }


@dataclass
class DatasetData:
    symbol: str
    timeframe: str
    trades: List[BacktestTrade]
    setups: List[SetupRecord]
    run: object

    def by_setup(self) -> Dict[str, List[BacktestTrade]]:
        out: Dict[str, List[BacktestTrade]] = {t: [] for t in SETUP_TYPES}
        for tr in self.trades:
            out.setdefault(tr.setup_type, []).append(tr)
        return out

    def by_regime(self) -> Dict[str, List[BacktestTrade]]:
        out: Dict[str, List[BacktestTrade]] = {}
        for tr in self.trades:
            out.setdefault(tr.regime, []).append(tr)
        return out


def _profit_factor(wins_sum, losses_sum) -> Optional[float]:
    """wins_sum/losses_sum are the signed net-return sums of wins and losses."""
    if losses_sum < 0:
        return wins_sum / abs(losses_sum)
    if wins_sum > 0:
        return float("inf")
    return None


def slice_stats(trades: List[BacktestTrade], setup_count: int, label: str) -> SliceStats:
    """Compute aggregate stats over a list of completed trades."""
    n = len(trades)
    wins = [t for t in trades if t.net_return > 0]
    losses = [t for t in trades if t.net_return < 0]
    expirations = sum(
        1 for t in trades if t.exit_reason in ("EXPIRED", "INVALIDATED", "END_OF_DATA")
    )
    rets = [t.net_return for t in trades]
    avg_ret = (sum(rets) / n) if n else 0.0
    expect = avg_ret if n else None
    if n:
        gw = sum(t.net_return for t in wins)
        gl = sum(t.net_return for t in losses)
        pf = _profit_factor(gw, gl)
        wr = len(wins) / n
        hold = sum(t.holding_bars for t in trades) / n
        fees = sum(t.fees for t in trades)
        slip = sum(t.slippage for t in trades)
        cheap = sum((t.gross_return - t.net_return) for t in trades)
    else:
        pf = None
        wr = 0.0
        hold = None
        fees = 0.0
        slip = 0.0
        cheap = 0.0
    return SliceStats(
        label=label,
        n_trades=n,
        n_setups=setup_count,
        wins=len(wins),
        losses=len(losses),
        expirations=expirations,
        avg_return=avg_ret,
        expectancy=expect,
        profit_factor=pf,
        win_rate=wr,
        avg_holding_bars=hold,
        fees=fees,
        slippage=slip,
        gross_to_net_gap=cheap,
    )


def count_outcomes(setups: List[SetupRecord]) -> Dict[str, int]:
    out: Dict[str, int] = {
        "detected": len(setups),
        "qualified": sum(1 for s in setups if s.outcome in ("QUALIFIED_NO_TRADE", "COMPLETED", "EXPIRED", "INVALIDATED")),
        "completed": sum(1 for s in setups if s.outcome == "COMPLETED"),
    }
    return out


class DiagnosticsSet:
    """Holds immutable per-dataset diagnostics for one or many datasets."""

    def __init__(self, datasets: List[DatasetData], settings: Optional[Settings] = None) -> None:
        self.datasets = datasets
        self._metrics = MetricsEngine()

    def all_trades(self) -> List[BacktestTrade]:
        out: List[BacktestTrade] = []
        for d in self.datasets:
            out.extend(d.trades)
        return out

    def all_setups(self) -> List[SetupRecord]:
        out: List[SetupRecord] = []
        for d in self.datasets:
            out.extend(d.setups)
        return out


def validate_dataset(
    symbol: str,
    timeframe: str,
    settings: Optional[Settings] = None,
    df: Optional[pd.DataFrame] = None,
) -> DatasetData:
    """Run the frozen baseline ONCE for a dataset and capture raw records."""
    settings = settings or get_settings()
    if df is None:
        df = CandleStore(settings).load(symbol, timeframe)
    df = df.sort_values("open_time").reset_index(drop=True)
    engine = build_baseline_engine(settings, progressive=True)
    run = engine.run(symbol, timeframe, df.copy(), run_key=f"diag|{symbol}|{timeframe}")
    return DatasetData(symbol=symbol, timeframe=timeframe, trades=run.trades, setups=run.setups, run=run)  # fmt: skip


DEFAULT_DATASETS_SPEC = [
    ("BTC/USDT", "4H"),
    ("BTC/USDT", "1D"),
    ("ETH/USDT", "4H"),
    ("ETH/USDT", "1D"),
]