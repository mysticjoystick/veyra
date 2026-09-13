"""Backtest execution semantics (deterministic, conservative).

This is where Veyra's *hypothetical* entry/exit mechanics live. The rules are
deliberately simple and defensive; they are NOT optimised to look good.

Key decisions
-------------
* Entry fill: at the OPEN of the bar following the decision bar (`next_open`).
  This avoids using the decision bar's high/low to both trigger and price the
  entry within the same candle (which would be an intra-bar look-ahead).
* Entry price for a LONG is the open (buy); for a SHORT the open (sell). All
  prices are then adjusted for slippage/spread configured by the caller.
* Conservative ambiguous-candle rule: when a single candle's range touches BOTH
  the stop and the target (we cannot see intrabar order), we resolve to the
  outcome that is WORST for the strategy: the stop is assumed to be hit first.
  This biases research toward caution, never toward profit.
* Stop/target exit at the exact stop/target price; expiry closes at the bar
  close; invalidation closes at the bar close.

All functions are pure and deterministic so identical inputs yield identical
outputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Bar:
    """Minimal OHLC for execution; fields named to avoid clashing with domain."""

    open: float
    high: float
    low: float
    close: float


def effective_entry_price(
    bar_open: float,
    side: str,  # "LONG" | "SHORT"
    slippage_pct: float,
    spread_pct: float,
) -> float:
    """Price at which a position entered at the bar open is filled.

    A long buys at the offered side of the spread; a short sells at the bid
    side. Slippage always acts against the trader.
    """
    if side == "LONG":
        base = bar_open * (1.0 + spread_pct / 2.0)
        return base * (1.0 + slippage_pct)
    base = bar_open * (1.0 - spread_pct / 2.0)
    return base * (1.0 - slippage_pct)


def compute_exit(
    bar: Bar,
    side: str,  # "LONG" | "SHORT"
    stop: float,
    target: Optional[float],
    ambiguous_candle_policy: str = "stop_first",
) -> Optional[tuple]:
    """Determine whether a position exits on this bar.

    Returns None if no exit, else (exit_price, reason) where reason is STOP or
    TARGET. A None target means there is no profit objective; the position can
    only exit by stop (or expiry/invalidation handled by the caller).
    """
    if side == "LONG":
        bad = bar.low <= stop
        good = (bar.high >= target) if target is not None else False
        if bad and good:
            # Both touched this candle and the intrabar order is unknown.
            if ambiguous_candle_policy == "stop_first":
                return (stop, "STOP")
            return (target, "TARGET")
        if bad:
            return (stop, "STOP")
        if good:
            return (target, "TARGET")
        return None

    # SHORT: stop is above, target below.
    bad = bar.high >= stop
    good = (bar.low <= target) if target is not None else False
    if bad and good:
        if ambiguous_candle_policy == "stop_first":
            return (stop, "STOP")
        return (target, "TARGET")
    if bad:
        return (stop, "STOP")
    if good:
        return (target, "TARGET")
    return None


def exit_price_adjusted(
    exit_price: float,
    side: str,
    slippage_pct: float,
    spread_pct: float,
    reason: str,
) -> float:
    """Apply costs against the rough side of the market for an exit fill."""
    direction = 1.0 if side == "LONG" else -1.0
    # Slippage/spread always move the *exit* price against the trader.
    return exit_price * (1.0 - direction * (slippage_pct + spread_pct / 2.0))


def duration_bars(entry_ts: int, exit_ts: int, interval_seconds: int) -> int:
    """Whole bars held between entry and exit timestamps."""
    if interval_seconds <= 0:
        return 0
    return int((exit_ts - entry_ts) // interval_seconds)