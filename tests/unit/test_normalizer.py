"""Tests for the normalization layer."""

from __future__ import annotations

import pandas as pd
import pytest

from veyra.data.normalizer import Normalizer
from veyra.error import DataNormalizationError


def test_normalize_maps_canonical_columns():
    norm = Normalizer()
    records = [
        {
            "open_time": 1000,
            "open": 10.0,
            "high": 12.0,
            "low": 9.0,
            "close": 11.0,
            "volume": 500.0,
        }
    ]
    candles = norm.normalize(records, "BTC/USDT", "4H")
    assert len(candles) == 1
    c = candles[0]
    assert c.symbol == "BTC/USDT"
    assert c.timeframe == "4H"
    assert c.open_time == 1000
    assert c.open == 10.0
    assert c.high == 12.0
    assert c.low == 9.0
    assert c.close == 11.0
    assert c.volume == 500.0


def test_normalize_handles_alias_columns():
    norm = Normalizer()
    records = [
        {
            "timestamp": 2000,
            "o": 5.0,
            "h": 6.0,
            "l": 4.0,
            "c": 5.5,
            "v": 100.0,
        }
    ]
    candles = norm.normalize(records, "ETH/USDT", "1D")
    assert candles[0].open_time == 2000
    assert candles[0].close == 5.5


def test_normalize_converts_millisecond_timestamps():
    norm = Normalizer()
    records = [{"open_time": 1_600_512_000_000, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}]
    candles = norm.normalize(records, "BTC/USDT", "4H")
    assert candles[0].open_time == 1_600_512_000  # ms -> s


def test_normalize_orders_and_dedupes():
    norm = Normalizer()
    records = [
        {"open_time": 2000, "open": 3, "high": 4, "low": 2, "close": 3.5, "volume": 1},
        {"open_time": 1000, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10},
        {"open_time": 2000, "open": 3, "high": 4, "low": 2, "close": 9.0, "volume": 1},
    ]
    candles = norm.normalize(records, "BTC/USDT", "4H")
    # sorted ascending; duplicate 2000 keeps last occurrence (close 9.0)
    assert [c.open_time for c in candles] == [1000, 2000]
    assert candles[1].close == 9.0


def test_normalize_empty_input_returns_empty():
    assert Normalizer().normalize([], "BTC/USDT", "4H") == []
    assert Normalizer().normalize(None, "BTC/USDT", "4H") == []
    assert Normalizer().normalize(pd.DataFrame(), "BTC/USDT", "4H") == []


def test_normalize_missing_required_column_raises():
    with pytest.raises(DataNormalizationError):
        Normalizer().normalize(
            [{"open_time": 1, "open": 1, "high": 2, "low": 1, "close": 1}], "B", "4H"
        )


def test_normalize_nonnumeric_ohlcv_raises():
    with pytest.raises(DataNormalizationError):
        Normalizer().normalize(
            [{"open_time": 1, "open": "abc", "high": 2, "low": 1, "close": 1, "volume": 1}],
            "B",
            "4H",
        )


def test_normalize_invalid_timestamp_raises():
    with pytest.raises(DataNormalizationError):
        Normalizer().normalize(
            [{"open_time": "not-a-date", "open": 1, "high": 2, "low": 1, "close": 1, "volume": 1}],
            "B",
            "4H",
        )