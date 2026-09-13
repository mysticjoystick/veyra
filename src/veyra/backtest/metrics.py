"""Deterministic backtest metrics.

Computes trade statistics, risk/performance, and opportunity metrics from a set
of completed trades and setup records. All measures are deterministic and handle
edge cases (zero trades, zero wins, zero losses, no drawdown) by returning
0.0 / 0 / None rather than NaN or fabricated values.

Expectancy and profit factor describe the sample; they are NOT calibrated
probability claims.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import BacktestTrade, SetupRecord


@dataclass
class TradeStats:
    total_setups: int = 0
    qualified_setups: int = 0
    triggered_setups: int = 0
    completed_trades: int = 0
    wins: int = 0
    losses: int = 0
    breakeven: int = 0
    win_rate: float = 0.0
    average_win: float = 0.0
    average_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    average_return: float = 0.0
    median_return: float = 0.0


@dataclass
class RiskStats:
    expectancy: float = 0.0
    profit_factor: Optional[float] = None
    cumulative_return: float = 0.0
    max_drawdown: float = 0.0
    average_drawdown: float = 0.0
    longest_losing_streak: int = 0
    longest_winning_streak: int = 0
    average_holding_bars: float = 0.0
    max_holding_bars: int = 0
    total_fees: float = 0.0
    total_slippage: float = 0.0


@dataclass
class OpportunityStats:
    qualification_rate: float = 0.0
    trigger_rate: float = 0.0
    invalidation_rate: float = 0.0
    expiry_rate: float = 0.0
    setups_per_bar: float = 0.0
    trades_per_bar: float = 0.0


@dataclass
class MetricsResult:
    trades: TradeStats = field(default_factory=TradeStats)
    risk: RiskStats = field(default_factory=RiskStats)
    opportunity: OpportunityStats = field(default_factory=OpportunityStats)
    by_setup_type: Dict[str, "MetricBucket"] = field(default_factory=dict)
    by_timeframe: Dict[str, "MetricBucket"] = field(default_factory=dict)
    by_regime: Dict[str, "MetricBucket"] = field(default_factory=dict)
    by_score_bucket: Dict[str, "MetricBucket"] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "trades": self.trades.__dict__,
            "risk": self.risk.__dict__,
            "opportunity": self.opportunity.__dict__,
            "by_setup_type": {k: v.to_dict() for k, v in self.by_setup_type.items()},
            "by_timeframe": {k: v.to_dict() for k, v in self.by_timeframe.items()},
            "by_regime": {k: v.to_dict() for k, v in self.by_regime.items()},
            "by_score_bucket": {k: v.to_dict() for k, v in self.by_score_bucket.items()},
        }


@dataclass
class MetricBucket:
    """A trimmed metrics summary for a single dimension slice."""

    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    average_return: float = 0.0
    cumulative_return: float = 0.0
    profit_factor: Optional[float] = None
    expectancy: float = 0.0

    def to_dict(self) -> dict:
        return {
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": self.win_rate,
            "average_return": self.average_return,
            "cumulative_return": self.cumulative_return,
            "profit_factor": self.profit_factor,
            "expectancy": self.expectancy,
        }


class MetricsEngine:
    def compute(
        self,
        trades: List[BacktestTrade],
        setups: List[SetupRecord],
        candle_count: int = 0,
        score_buckets: Optional[List[tuple]] = None,
    ) -> MetricsResult:
        """Produce deterministic metrics from completed trades + setup records.

        score_buckets: list of (label, low, high) inclusive ranges, e.g.
            [("50-59", 50, 59), ...]. Uses normalised score.
        """
        if score_buckets is None:
            score_buckets = [
                ("0-49", 0, 49),
                ("50-59", 50, 59),
                ("60-69", 60, 69),
                ("70-79", 70, 79),
                ("80-89", 80, 89),
                ("90-100", 90, 100),
            ]

        result = MetricsResult()
        result.trades = self._trade_stats(trades)
        result.trades.total_setups = len(setups)
        result.trades.qualified_setups = sum(
            1 for s in setups if s.outcome in ("COMPLETED", "QUALIFIED_NO_TRADE")
        )
        result.risk = self._risk_stats(trades)
        result.opportunity = self._opportunity_stats(setups, trades, candle_count)
        result.by_setup_type = self._bucket_by(trades, lambda t: t.setup_type)
        result.by_timeframe = self._bucket_by(trades, lambda t: t.timeframe)
        result.by_regime = self._bucket_by(trades, lambda t: t.regime)
        result.by_score_bucket = self._bucket_by(
            trades, lambda t: _score_bucket(t.score_normalized, score_buckets)
        )
        return result

    # -- Trade statistics -------------------------------------------------

    def _trade_stats(self, trades: List[BacktestTrade]) -> TradeStats:
        stats = TradeStats()
        returns = [t.net_return for t in trades]
        stats.completed_trades = len(trades)
        stats.triggered_setups = len(trades)
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r < 0]
        even = [r for r in returns if r == 0]
        stats.wins = len(wins)
        stats.losses = len(losses)
        stats.breakeven = len(even)
        stats.win_rate = (len(wins) / len(returns)) if returns else 0.0
        stats.average_win = (sum(wins) / len(wins)) if wins else 0.0
        stats.average_loss = (sum(losses) / len(losses)) if losses else 0.0
        stats.largest_win = max(wins) if wins else 0.0
        stats.largest_loss = min(losses) if losses else 0.0
        stats.average_return = statistics.mean(returns) if returns else 0.0
        stats.median_return = statistics.median(returns) if returns else 0.0
        return stats

    def _risk_stats(self, trades: List[BacktestTrade]) -> RiskStats:
        risk = RiskStats()
        n = len(trades)
        if not n:
            return risk
        returns = [t.net_return for t in trades]
        risk.expectancy = statistics.mean(returns)

        gross_wins = sum(r for r in returns if r > 0)
        gross_losses = abs(sum(r for r in returns if r < 0))
        if gross_losses > 0:
            risk.profit_factor = gross_wins / gross_losses
        elif gross_wins > 0:
            risk.profit_factor = float("inf")  # no losses at all
        else:
            risk.profit_factor = None

        # Cumulative return (product of (1+r) - 1) and drawdown from equity.
        equity = 1.0
        peak = 1.0
        drawdowns: List[float] = []
        for r in returns:
            equity *= (1.0 + r)
            peak = max(peak, equity)
            dd = (equity - peak) / peak if peak > 0 else 0.0
            drawdowns.append(dd)
        risk.cumulative_return = equity - 1.0
        risk.max_drawdown = min(drawdowns) if drawdowns else 0.0
        if drawdowns:
            risk.average_drawdown = sum(drawdowns) / len(drawdowns)

        # Streaks.
        longest_win, longest_loss = 0, 0
        cur_win, cur_loss = 0, 0
        for r in returns:
            if r > 0:
                cur_win += 1
                cur_loss = 0
                longest_win = max(longest_win, cur_win)
            elif r < 0:
                cur_loss += 1
                cur_win = 0
                longest_loss = max(longest_loss, cur_loss)
            else:
                cur_win, cur_loss = 0, 0
        risk.longest_winning_streak = longest_win
        risk.longest_losing_streak = longest_loss

        risk.average_holding_bars = statistics.mean(
            [t.holding_bars for t in trades]
        )
        risk.max_holding_bars = max(t.holding_bars for t in trades)
        risk.total_fees = sum(t.fees for t in trades)
        risk.total_slippage = sum(t.slippage for t in trades)
        return risk

    def _opportunity_stats(
        self,
        setups: List[SetupRecord],
        trades: List[BacktestTrade],
        candle_count: int,
    ) -> OpportunityStats:
        opp = OpportunityStats()
        total = len(setups)
        if total:
            qualified = sum(
                1 for s in setups
                if s.outcome in ("COMPLETED", "QUALIFIED_NO_TRADE")
            )
            opp.qualification_rate = qualified / total
            opp.trigger_rate = len(trades) / total
            inv = sum(1 for s in setups if s.outcome == "INVALIDATED")
            exp = sum(1 for s in setups if s.outcome == "EXPIRED")
            opp.invalidation_rate = inv / total
            opp.expiry_rate = exp / total
        if candle_count > 0:
            opp.setups_per_bar = total / candle_count
            opp.trades_per_bar = len(trades) / candle_count
        return opp

    # -- Breakdowns -------------------------------------------------------

    def _bucket_by(self, trades, keyfn) -> Dict[str, MetricBucket]:
        return _bucket_trades(trades, keyfn)


def _score_bucket(score: int, buckets: List[tuple]) -> str:
    for label, low, high in buckets:
        if low <= score <= high:
            return label
    return str(score)


def _bucket_trades(trades: List[BacktestTrade], keyfn) -> Dict[str, MetricBucket]:
    groups: Dict[str, List[BacktestTrade]] = {}
    for t in trades:
        groups.setdefault(keyfn(t), []).append(t)
    out: Dict[str, MetricBucket] = {}
    for key, lst in groups.items():
        bucket = MetricBucket()
        bucket.trades = len(lst)
        returns = [t.net_return for t in lst]
        bucket.wins = sum(1 for r in returns if r > 0)
        bucket.losses = sum(1 for r in returns if r < 0)
        bucket.win_rate = (bucket.wins / len(returns)) if returns else 0.0
        bucket.average_return = statistics.mean(returns) if returns else 0.0
        equity = 1.0
        for r in returns:
            equity *= (1.0 + r)
        bucket.cumulative_return = equity - 1.0
        gross_wins = sum(r for r in returns if r > 0)
        gross_losses = abs(sum(r for r in returns if r < 0))
        if gross_losses > 0:
            bucket.profit_factor = gross_wins / gross_losses
        elif gross_wins > 0:
            bucket.profit_factor = float("inf")
        else:
            bucket.profit_factor = None
        bucket.expectancy = statistics.mean(returns) if returns else 0.0
        out[key] = bucket
    return out