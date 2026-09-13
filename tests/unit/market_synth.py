"""Synthetic market data generators for engine tests.

Produce deterministic candle frames engineered to yield known Trend,
Structure, Momentum, Volume, and Volatility conditions WITHOUT relying on
live market data.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd


def _frame_from_columns(n: int, start_ts: int, close: np.ndarray, **extra) -> pd.DataFrame:
    """Build a canonical candle DataFrame from a close array with synthetic
    open/high/low and (optional) volume derived deterministically."""
    n = len(close)
    high = np.maximum(close, np.array([c + 2.0 for c in close])) * 1.0
    # Simplify: high = close + 1, low = close - 1, open ramps toward close.
    opens = np.concatenate(([close[0]], close[:-1]))
    highs = np.maximum(opens, close) + 0.5
    lows = np.minimum(opens, close) - 0.5
    open_time = np.arange(start_ts, start_ts + n * 14400, 14400, dtype=np.int64)
    volume = extra.pop("volume", np.full(n, 1000.0))
    df = pd.DataFrame(
        {
            "open_time": open_time,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": close,
            "volume": volume,
        }
    )
    return df


def _trend_closes(n: int, start: float, step: float) -> np.ndarray:
    return np.array([start + step * i for i in range(n)], dtype=float)


def bullish_frame(n: int = 260, start_ts: int = 1_000_000_000) -> pd.DataFrame:
    """Clean, steady uptrend (higher highs, higher lows)."""
    closes = _trend_closes(n, 100.0, 0.5)
    return _frame_from_columns(n, start_ts, closes)


def bearish_frame(n: int = 260, start_ts: int = 1_000_000_000) -> pd.DataFrame:
    """Clean, steady downtrend (lower highs, lower lows)."""
    closes = _trend_closes(n, 200.0, -0.5)
    return _frame_from_columns(n, start_ts, closes)


def sideways_frame(n: int = 260, start_ts: int = 1_000_000_000) -> pd.DataFrame:
    """Oscillating range (neutral trend, controlled volatility)."""
    rng = np.arange(n)
    close = 150.0 + 10.0 * np.sin(rng * 0.2)
    return _frame_from_columns(n, start_ts, close)


def volatile_frame(n: int = 260, start_ts: int = 1_000_000_000) -> pd.DataFrame:
    """Large-amplitude swings producing HIGH/EXTREME volatility."""
    rng = np.arange(n)
    close = 150.0 + 40.0 * np.sin(rng * 0.1)
    return _frame_from_columns(n, start_ts, close)


def expansion_frame(n: int = 260, start_ts: int = 1_000_000_000) -> pd.DataFrame:
    """Flat then sharply *accelerating* expansion at the end.

    Per-bar range grows over the tail so the current ATR is much larger
    than ATR ~period bars earlier -> HIGH/EXTREME volatility state.
    """
    base = np.full(n, 150.0)
    tail = int(n * 0.4)
    # Exponential growth of per-bar move: last bars move far more than early.
    k = np.linspace(0.0, 1.0, tail)
    increments = 1.0 + (k ** 3) * 80.0  # from ~1 to ~81 per bar
    step = np.cumsum(increments)
    close = base.copy()
    close[-tail:] = base[-tail - 1] + step
    return _frame_from_columns(n, start_ts, close)


def buy_volume_frame(n: int = 40, start_ts: int = 1_000_000_000) -> pd.DataFrame:
    """An uptrend with an expanding volume burst at the end."""
    closes = _trend_closes(n, 100.0, 0.3)
    volume = np.full(n, 1000.0)
    volume[-3:] = 2000.0
    return _frame_from_columns(n, start_ts, closes, volume=volume)


def insufficient_frame(n: int = 30, start_ts: int = 1_000_000_000) -> pd.DataFrame:
    """Too few candles for EMA200 / MACD / ATR to be meaningful."""
    closes = _trend_closes(n, 100.0, 0.5)
    return _frame_from_columns(n, start_ts, closes)


def higher_highs_higher_lows_frame(
    n: int = 260, start_ts: int = 1_000_000_000
) -> pd.DataFrame:
    """Explicit HH then HL structure pattern (bullish).

    Oscillates upward: each swing high is higher than the prior swing high
    and each swing low is higher than the prior swing low, so a real
    alternating swing structure is present (unlike a pure ramp).
    """
    cycles = 12
    points_per = max(n // cycles, 2)
    vals = []
    phase = 0.0
    base = 100.0
    for cyc in range(cycles):
        seg = np.linspace(0.0, np.pi, points_per)
        # ascending sine segment lifting both peak and trough each cycle
        wave = np.sin(seg) * 5.0 + base + cyc * 8.0
        vals.extend(wave.tolist())
    close = np.array(vals[:n], dtype=float)
    return _frame_from_columns(n, start_ts, close)


def hh_hl_swings_frame(
    points_per: int = 6, cycles: int = 8, start_ts: int = 1_000_000_000
) -> pd.DataFrame:
    """Discrete, clearly separable higher-highs & higher-lows zigzag."""
    closes = []
    level = 100.0
    # Alternate up (to a new high) then down (to a higher low).
    for cyc in range(cycles):
        # up-leg: HH
        closes.extend(np.linspace(level, level + 5.0, points_per))
        level += 5.0
        # down-leg: HL (pull back but not all the way)
        closes.extend(np.linspace(level, level - 3.0, points_per))
        level -= 3.0
    closes = np.array(closes, dtype=float)
    return _frame_from_columns(len(closes), start_ts, closes)


def lh_ll_swings_frame(
    points_per: int = 6, cycles: int = 8, start_ts: int = 1_000_000_000
) -> pd.DataFrame:
    """Discrete lower-highs & lower-lows (bearish) zigzag."""
    closes = []
    level = 300.0
    for cyc in range(cycles):
        # down-leg: LH (falls to a new low, partially)
        closes.extend(np.linspace(level, level - 5.0, points_per))
        level -= 5.0
        # up-leg: LL (bounces but stays below the prior lower low)
        closes.extend(np.linspace(level, level + 3.0, points_per))
        level += 3.0
    closes = np.array(closes, dtype=float)
    return _frame_from_columns(len(closes), start_ts, closes)


def bull_continuation_frame(
    n: int = 300, start_ts: int = 1_000_000_000
) -> pd.DataFrame:
    """Uptrend with rising HH/HL swings that ENDS on an up-leg.

    Guarantees positive final momentum (unlike a frame ending on a trough)
    while keeping a long, structurally bullish history for EMA200 warmup.
    """
    points_per = 12
    closes = []
    level = 100.0
    produced = 0
    # Build full up+down swing pairs until we are near the target length.
    while produced + 2 * points_per <= n - points_per:
        closes.extend(np.linspace(level, level + 5.0, points_per))  # up: HH
        level += 5.0
        closes.extend(np.linspace(level, level - 3.0, points_per))  # down: HL
        level -= 3.0
        produced += 2 * points_per
    # Final up-leg to ensure the last candle has positive momentum.
    closes.extend(np.linspace(level, level + 6.0, points_per))
    closes = np.array(closes[:n], dtype=float)
    return _frame_from_columns(len(closes), start_ts, closes)


def breaking_up_frame(n: int = 60, start_ts: int = 1_000_000_000) -> pd.DataFrame:
    """A structure that closes above a recent swing high (BOS up)."""
    rng = np.arange(n)
    close = 100.0 + np.where(rng < 30, 5.0 * np.sin(rng * 0.3), 8.0 + 0.3 * (rng - 30))
    return _frame_from_columns(n, start_ts, close)