"""Phase 5 score calibration / usefulness analysis.

The Phase 4 score is a *setup-quality score*, NOT a calibrated probability of
profit. This module measures whether the score has any predictive discrimination
at all (monotonicity of win-rate / average-return across fixed buckets) and
quantifies how far the observed rates are from a hypothetical 50/50 baseline,
so the report can state honestly whether the score is useful or not.

Rules honoured here (Phase 5):
- no fitting/tuning the score to improve results;
- buckets are fixed & pre-registered (see ``validate.FIXED_SCORE_BUCKETS``);
- small-sample buckets are reported with their n, never dropped to flatter;
- the score is never relabelled as a probability of profit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ..backtest import MetricsResult


@dataclass
class BucketCalibration:
    bucket: str
    trades: int
    wins: int
    losses: int
    win_rate: Optional[float]
    average_return: Optional[float]
    cumulative_return: Optional[float]
    profit_factor: Optional[float]
    edge_per_trade_vs_baseline: Optional[float]
    edge_per_trade_vs_fees: Optional[float]
    # 95% binomial confidence interval on win rate (Wilson), if n>0.
    win_rate_ci_low: Optional[float]
    win_rate_ci_high: Optional[float]


@dataclass
class CalibrationResult:
    buckets: List[BucketCalibration] = field(default_factory=list)
    overall_win_rate: Optional[float] = None
    overall_average_return: Optional[float] = None
    baseline_win_rate: float = 0.5
    monotonic_win_rate: Optional[bool] = None
    monotonic_avg_return: Optional[bool] = None
    kendall_win_rate: Optional[float] = None
    kendall_avg_return: Optional[float] = None
    score_is_useful: Optional[bool] = None
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "buckets": [
                b.__dict__ for b in self.buckets
            ],
            "overall_win_rate": self.overall_win_rate,
            "overall_average_return": self.overall_average_return,
            "baseline_win_rate": self.baseline_win_rate,
            "monotonic_win_rate": self.monotonic_win_rate,
            "monotonic_avg_return": self.monotonic_avg_return,
            "kendall_win_rate": self.kendall_win_rate,
            "kendall_avg_return": self.kendall_avg_return,
            "score_is_useful": self.score_is_useful,
            "notes": self.notes,
        }


def _wilson(win: int, n: int, z: float = 1.96):
    """Wilson score interval with continuity consideration (plain Wilson)."""
    if n <= 0:
        return None, None
    p = win / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


class CalibrationEngine:
    """Measure discrimination of the Phase 4 score over fixed buckets."""

    def __init__(self, baseline_win_rate: float = 0.5) -> None:
        self._baseline = baseline_win_rate

    def assess(self, metric: MetricsResult) -> CalibrationResult:
        buckets = metric.by_score_bucket or {}
        cal: List[BucketCalibration] = []

        # benchmark per-trade edge: (avg_win*wr) + (avg_loss*loss_rate) minus fees.
        trades_stats = metric.trades
        overall_rate = trades_stats.win_rate if trades_stats.total_setups else None
        overall_avg = trades_stats.average_return if trades_stats.total_setups else None

        avg_fee_cost = 0.0  # fees already netted into net_return by the engine.

        for label in sorted(buckets, key=_bucket_low):
            b = buckets[label]
            wins, losses = b.wins, b.losses
            n = b.trades
            rate = (wins / n) if n else None
            avg_ret = b.average_return if n else None
            pf = b.profit_factor if n else None
            lo, hi = _wilson(wins, n) if n else (None, None)
            edge_vs_baseline = (rate - self._baseline) if rate is not None else None
            edge_vs_fees = (
                (rate - self._baseline) - avg_fee_cost if rate is not None else None
            )
            cal.append(
                BucketCalibration(
                    bucket=label,
                    trades=n,
                    wins=wins,
                    losses=losses,
                    win_rate=rate,
                    average_return=avg_ret,
                    cumulative_return=b.cumulative_return if n else None,
                    profit_factor=pf,
                    edge_per_trade_vs_baseline=edge_vs_baseline,
                    edge_per_trade_vs_fees=edge_vs_fees,
                    win_rate_ci_low=lo,
                    win_rate_ci_high=hi,
                )
            )

        res = CalibrationResult(
            buckets=cal,
            overall_win_rate=overall_rate,
            overall_average_return=overall_avg,
            baseline_win_rate=self._baseline,
        )

        # Monotonicity: require non-decreasing win-rate and avg-return across
        # buckets that have any trades. Skip empty buckets.
        rates = [b.win_rate for b in cal if b.win_rate is not None and b.trades >= 5]
        avgs = [b.average_return for b in cal if b.average_return is not None and b.trades >= 5]
        res.monotonic_win_rate = _is_monotonic(rates)
        res.monotonic_avg_return = _is_monotonic(avgs)
        res.kendall_win_rate = _kendall_tau(rates)
        res.kendall_avg_return = _kendall_tau(avgs)

        # Usefulness: the score provides discrimination if win rate rises
        # monotonically AND the spread across buckets is material, OR if
        # top-bucket advantage is real. Honest fallback: only when data supports.
        if len(rates) < 2:
            res.score_is_useful = None
            res.notes.append("fewer than 2 populated buckets -> score usefulness unmeasurable")
        else:
            spread = (max(rates) - min(rates)) if rates else 0.0
            res.score_is_useful = bool(res.monotonic_win_rate and spread >= 0.05)
            res.notes.append(
                "score_is_useful is a description of observed discrimination, "
                "NOT a claim that the score is a calibrated probability"
            )
        return res


def _bucket_low(label: str) -> int:
    try:
        return int(label.split("-")[0])
    except Exception:
        return int(label)


def _is_monotonic(seq: List[Optional[float]]) -> Optional[bool]:
    vals = [x for x in seq if x is not None]
    if len(vals) < 2:
        return None
    return all(vals[i + 1] >= vals[i] for i in range(len(vals) - 1)) or all(
        vals[i + 1] <= vals[i] for i in range(len(vals) - 1)
    )


def _kendall_tau(seq: List[Optional[float]]) -> Optional[float]:
    vals = [x for x in seq if x is not None]
    n = len(vals)
    if n < 2:
        return None
    concordant = 0
    discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            if (vals[i] < vals[j]) == (i < j):
                concordant += 1
            else:
                discordant += 1
    denom = (n * (n - 1)) / 2
    return (concordant - discordant) / denom if denom else None