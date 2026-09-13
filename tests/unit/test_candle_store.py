"""Tests for the candle store (Parquet persistence)."""

from __future__ import annotations

import pandas as pd

from veyra.data.candle_store import CandleStore

from .helpers import candles_to_frame, make_candles


def test_store_write_then_load_roundtrip(candle_store):
    candles = make_candles(n=5)
    candle_store.write("BTC/USDT", "4H", candles)
    df = candle_store.load("BTC/USDT", "4H")
    assert len(df) == 5
    assert list(df.columns) == ["open_time", "open", "high", "low", "close", "volume"]
    assert df["close"].iloc[-1] == candles[-1].close


def test_store_deduplicates_overlapping_ranges(candle_store):
    first = make_candles(n=5, start_ts=1000)
    overlapping = make_candles(n=5, start_ts=1000 + 3 * 14400)
    candle_store.write("BTC/USDT", "4H", first)
    candle_store.write("BTC/USDT", "4H", overlapping)
    df = candle_store.load("BTC/USDT", "4H")
    assert len(df["open_time"].unique()) == len(df)


def test_store_load_empty_when_absent(candle_store):
    df = candle_store.load("ETH/USDT", "1D")
    assert df.empty
    assert not candle_store.has_any("ETH/USDT", "1D")


def test_store_write_empty_is_noop(candle_store):
    assert candle_store.write("BTC/USDT", "4H", []) == 0
    assert not candle_store.has_any("BTC/USDT", "4H")