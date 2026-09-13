"""Tests for the MomentumEngine."""

from __future__ import annotations

from veyra.domain import IndicatorState, MomentumState
from veyra.market.momentum import MomentumEngine

from .market_synth import bearish_frame, bullish_frame, insufficient_frame


def test_uptrend_momentum_positive():
    eng = MomentumEngine(rsi_period=14, macd_fast=12, macd_slow=26, macd_signal=9)
    res = eng.analyze(bullish_frame(n=100, start_ts=1_000_000_000))
    assert res.meta["state"] == IndicatorState.READY.value
    assert res.meta["momentum"] == MomentumState.POSITIVE.value
    assert 0 <= res.meta["macd_histogram"]


def test_downtrend_momentum_negative():
    eng = MomentumEngine(rsi_period=14, macd_fast=12, macd_slow=26, macd_signal=9)
    res = eng.analyze(bearish_frame(n=100, start_ts=1_000_000_000))
    assert res.meta["momentum"] == MomentumState.NEGATIVE.value
    assert res.meta["macd_histogram"] <= 0


def test_rsi_value_present():
    eng = MomentumEngine(rsi_period=14, macd_fast=12, macd_slow=26, macd_signal=9)
    res = eng.analyze(bullish_frame(n=100, start_ts=1_000_000_000))
    assert 0 <= res.meta["rsi"] <= 100


def test_insufficient_data():
    eng = MomentumEngine(rsi_period=14, macd_fast=12, macd_slow=26, macd_signal=9)
    res = eng.analyze(insufficient_frame(n=20, start_ts=1_000_000_000))
    assert res.meta["state"] == IndicatorState.INSUFFICIENT_DATA.value
    assert res.meta["momentum"] == MomentumState.UNKNOWN.value
    assert res.score == 0


def test_warmup_required_covers_macd_signal():
    eng = MomentumEngine(macd_slow=26, macd_signal=9)
    assert eng.warmup_required() >= 26 + 9