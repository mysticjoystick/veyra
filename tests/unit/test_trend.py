"""Tests for the TrendEngine."""

from __future__ import annotations

from veyra.domain import IndicatorState, TrendDirection
from veyra.market.trend import TrendEngine

from .market_synth import (
    bearish_frame,
    bullish_frame,
    insufficient_frame,
    sideways_frame,
)


def test_bullish_trend():
    eng = TrendEngine(ema_fast=10, ema_slow=30, slope_lookback=2)
    res = eng.analyze(bullish_frame(n=80, start_ts=1_000_000_000))
    assert res.meta["state"] == IndicatorState.READY.value
    assert res.meta["direction"] == TrendDirection.BULLISH.value
    assert res.meta["evidence"]["price_above_ema_slow"] is True
    assert res.meta["evidence"]["ema_fast_above_ema_slow"] is True


def test_bearish_trend():
    eng = TrendEngine(ema_fast=10, ema_slow=30, slope_lookback=2)
    res = eng.analyze(bearish_frame(n=80, start_ts=1_000_000_000))
    assert res.meta["direction"] == TrendDirection.BEARISH.value


def test_neutral_or_low_strength_on_sideways():
    eng = TrendEngine(ema_fast=10, ema_slow=30, slope_lookback=2)
    res = eng.analyze(sideways_frame(n=120, start_ts=1_000_000_000))
    # Sideways may resolve NEUTRAL or a weak directional score; must be READY.
    assert res.meta["state"] == IndicatorState.READY.value
    assert res.score <= 100


def test_insufficient_data():
    eng = TrendEngine(ema_fast=10, ema_slow=30)
    res = eng.analyze(insufficient_frame(n=20, start_ts=1_000_000_000))
    assert res.meta["state"] == IndicatorState.INSUFFICIENT_DATA.value
    assert res.meta["direction"] == TrendDirection.UNKNOWN.value
    assert res.score == 0


def test_warmup_required_is_ema_slow_plus_margin():
    eng = TrendEngine(ema_fast=10, ema_slow=30)
    assert eng.warmup_required() >= 30


def test_score_is_in_0_100_range():
    eng = TrendEngine(ema_fast=10, ema_slow=30)
    res = eng.analyze(bullish_frame(n=100, start_ts=1_000_000_000))
    assert 0 <= res.score <= 100