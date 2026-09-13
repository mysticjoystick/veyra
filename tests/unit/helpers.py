"""Test data helpers, importable from unit tests."""

from __future__ import annotations

import time

import pandas as pd

from veyra.domain.candle import Candle


def make_candles(
    symbol: str = "BTC/USDT",
    timeframe: str = "4H",
    n: int = 50,
    start_ts: int | None = None,
    base: float = 100.0,
) -> list[Candle]:
    if start_ts is None:
        start_ts = int(time.time()) - n * 14400
    candles = []
    for i in range(n):
        ts = start_ts + i * 14400
        o = base + i
        c = base + i + 0.5
        candles.append(
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                open_time=ts,
                open=o,
                high=max(o, c) + 1.0,
                low=min(o, c) - 1.0,
                close=c,
                volume=1000.0 + i,
            )
        )
    return candles


_COLS = ["open_time", "open", "high", "low", "close", "volume"]


def candles_to_frame(candles: list[Candle]) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(columns=_COLS)
    df = pd.DataFrame([c.to_dict() for c in candles])
    return df[_COLS].reset_index(drop=True)