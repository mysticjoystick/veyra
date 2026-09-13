"""Phase 4 metrics tests: deterministic, edge-case aware measures."""

from __future__ import annotations

import math

import pytest

from veyra.backtest.metrics import MetricsEngine
from veyra.backtest.models import BacktestTrade, SetupRecord


def _trade(trade_id, net, stype="BREAKOUT", regime="BULLISH", score_n=60,
           timeframe="4H", bars=5):
    return BacktestTrade(
        trade_id=trade_id, setup_key=trade_id, symbol="BTC/USDT",
        timeframe=timeframe, setup_type=stype, regime=regime,
        score=50, score_normalized=score_n, side="LONG",
        detection_ts=0, qualification_ts=0, entry_ts=1000, entry_price=100.0,
        invalidation="", exit_ts=1000 + bars * 14400, exit_price=0.0,
        exit_reason="TARGET", gross_return=net, fees=0.002, slippage=0.0,
        net_return=net, holding_bars=bars, max_favorable_excursion=1.0,
        max_adverse_excursion=0.5, overlap=False,
    )


def _setup(outcome, stype="BREAKOUT"):
    return SetupRecord(
        key=f"s_{outcome}", symbol="BTC/USDT", timeframe="4H", setup_type=stype,
        regime="BULLISH", detection_ts=0, score=50, score_normalized=50,
        side="LONG", interest_area_low=99.0, interest_area_high=101.0,
        invalidation="", final_state=outcome, outcome=outcome, trade_id=None,
    )


def test_zero_trades_edge_case():
    m = MetricsEngine().compute([], [], candle_count=100)
    assert m.trades.completed_trades == 0
    assert m.risk.expectancy == 0.0
    assert m.risk.profit_factor is None
    assert m.trades.win_rate == 0.0
    assert m.risk.max_drawdown == 0.0


def test_win_rate_and_return_stats():
    trades = [_trade("t1", 0.10), _trade("t2", -0.05), _trade("t3", 0.20), _trade("t4", -0.02)]
    m = MetricsEngine().compute(trades, [], 0)
    assert m.trades.wins == 2
    assert m.trades.losses == 2
    assert m.trades.win_rate == pytest.approx(0.5)
    assert m.trades.average_return == pytest.approx((0.10 - 0.05 + 0.20 - 0.02) / 4)
    assert m.trades.largest_win == pytest.approx(0.20)
    assert m.trades.largest_loss == pytest.approx(-0.05)


def test_cumulative_and_drawdown():
    trades = [_trade("t1", 0.5), _trade("t2", -0.5), _trade("t3", 0.5)]
    m = MetricsEngine().compute(trades, [], 0)
    assert m.risk.cumulative_return == pytest.approx((1.5 * 0.5 * 1.5) - 1.0)
    assert m.risk.max_drawdown <= 0.0  # drawdown is <= 0 by construction


def test_profit_factor_no_losses_is_inf():
    trades = [_trade("t1", 0.10), _trade("t2", 0.05)]
    m = MetricsEngine().compute(trades, [], 0)
    assert m.risk.profit_factor == float("inf")


def test_profit_factor_all_losses_is_zero():
    trades = [_trade("t1", -0.1), _trade("t2", -0.2)]
    m = MetricsEngine().compute(trades, [], 0)
    # No wins -> gross_wins == 0 -> profit factor 0.0 (no gross positive return).
    assert m.risk.profit_factor == 0.0


def test_streaks():
    trades = [
        _trade("t1", 0.1), _trade("t2", 0.1), _trade("t3", -0.1),
        _trade("t4", -0.1), _trade("t5", 0.1),
    ]
    m = MetricsEngine().compute(trades, [], 0)
    assert m.risk.longest_winning_streak == 2
    assert m.risk.longest_losing_streak == 2


def test_opportunity_stats_exclude_never_triggered_from_trades():
    setups = [
        _setup("COMPLETED"),
        _setup("COMPLETED"),
        _setup("QUALIFIED_NO_TRADE"),
        _setup("EXPIRED"),
        _setup("INVALIDATED"),
        _setup("DETECTED_ONLY"),
    ]
    trades = [_trade("t1", 0.1), _trade("t2", 0.1)]
    m = MetricsEngine().compute(trades, setups, candle_count=200)
    # Trades reflected only those that triggered.
    assert m.trades.completed_trades == 2
    assert m.trades.total_setups == 6
    assert m.opportunity.qualification_rate == pytest.approx(3 / 6)
    assert m.opportunity.trigger_rate == pytest.approx(2 / 6)
    assert m.opportunity.invalidation_rate == pytest.approx(1 / 6)
    assert m.opportunity.expiry_rate == pytest.approx(1 / 6)
    assert m.opportunity.setups_per_bar == pytest.approx(6 / 200)


def test_breakdown_by_setup_type_and_score_bucket():
    trades = [
        _trade("t1", 0.1, stype="BREAKOUT", score_n=55),
        _trade("t2", -0.1, stype="PULLBACK", score_n=65),
        _trade("t3", 0.2, stype="BREAKOUT", score_n=85),
    ]
    m = MetricsEngine().compute(trades, [], 0)
    assert m.by_setup_type["BREAKOUT"].trades == 2
    assert m.by_setup_type["PULLBACK"].trades == 1
    # Score buckets must be the normalised 0-100 bands.
    assert m.by_score_bucket["50-59"].trades == 1
    assert m.by_score_bucket["60-69"].trades == 1
    assert m.by_score_bucket["80-89"].trades == 1


def test_average_holding_and_fees():
    trades = [_trade("t1", 0.1, bars=3), _trade("t2", 0.1, bars=7)]
    m = MetricsEngine().compute(trades, [], 0)
    assert m.risk.average_holding_bars == pytest.approx(5.0)
    assert m.risk.max_holding_bars == 7
    assert m.risk.total_fees == pytest.approx(0.004)