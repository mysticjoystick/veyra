"""Tests for candle domain invariants and price zones."""

from __future__ import annotations

from veyra.domain.candle import Candle
from veyra.domain.setup import PriceZone
from veyra.domain import (
    MarketSide,
    Regime,
    SetupState,
    SetupType,
    SystemState,
    Timeframe,
)


def test_candle_valid_when_high_low_open_close_consistent():
    c = Candle(
        symbol="BTC/USDT",
        timeframe="4H",
        open_time=1,
        open=100.0,
        high=105.0,
        low=99.0,
        close=102.0,
        volume=1500.0,
    )
    assert c.is_valid


def test_candle_invalid_when_high_below_low():
    c = Candle(
        symbol="BTC/USDT",
        timeframe="4H",
        open_time=1,
        open=100.0,
        high=99.0,
        low=105.0,
        close=102.0,
        volume=1500.0,
    )
    assert not c.is_valid


def test_candle_invalid_when_close_outside_range():
    c = Candle(
        symbol="BTC/USDT",
        timeframe="4H",
        open_time=1,
        open=100.0,
        high=105.0,
        low=99.0,
        close=110.0,
        volume=1500.0,
    )
    assert not c.is_valid


def test_price_zone_contains():
    zone = PriceZone(100.0, 105.0)
    assert zone.contains(103.0)
    assert not zone.contains(99.0)
    assert not zone.contains(106.0)


def test_enums_are_stable():
    assert Timeframe.supported() == ["3m", "5m", "15m", "1H", "4H", "1D"]
    assert SetupState.QUALIFIED.value == "QUALIFIED"
    assert SystemState.WAIT.value == "WAIT"
    assert MarketSide.LONG.value == "LONG"
    assert SetupType.PULLBACK.value == "PULLBACK"
    assert Regime.BULL.value == "BULL"
