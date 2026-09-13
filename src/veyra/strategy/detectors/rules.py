"""Rule helpers shared by detectors.

Centralises small, deterministic predicates so each detector reads as a list
of explicit rules rather than raw-meta logic.
"""

from __future__ import annotations

from typing import Optional

from ...domain import MarketSide, Regime, SetupType
from ..candidate import SetupCandidate
from ..snapshot_view import SnapshotView


def trend_direction(view: SnapshotView) -> str:
    return view.meta("TREND").get("direction", "UNKNOWN")


def trend_strength(view: SnapshotView) -> float:
    return float(view.meta("TREND").get("strength", 0.0))


def structure_state(view: SnapshotView) -> str:
    return view.meta("STRUCTURE").get("structure", "UNKNOWN")


def structure_action(view: SnapshotView) -> str:
    return view.meta("STRUCTURE").get("action", "NONE")


def last_swing_high(view: SnapshotView, default: Optional[float] = None) -> Optional[float]:
    return view.meta("STRUCTURE").get("last_swing_high", default)


def last_swing_low(view: SnapshotView, default: Optional[float] = None) -> Optional[float]:
    return view.meta("STRUCTURE").get("last_swing_low", default)


def momentum_state(view: SnapshotView) -> str:
    return view.meta("MOMENTUM").get("momentum", "UNKNOWN")


def volume_state(view: SnapshotView) -> str:
    return view.meta("VOLUME").get("volume_state", "UNKNOWN")


def relative_volume(view: SnapshotView) -> float:
    rel = view.meta("VOLUME").get("relative_volume")
    return float(rel) if rel is not None else 0.0


def price_volume_confirmed(view: SnapshotView) -> bool:
    return bool(view.meta("VOLUME").get("price_volume_confirmation", False))


def volatility_state(view: SnapshotView) -> str:
    return view.meta("VOLATILITY").get("volatility_state", "UNKNOWN")


def atr_percent(view: SnapshotView) -> float:
    return float(view.meta("VOLATILITY").get("atr_percent", 0.0))


def rsi(view: SnapshotView) -> float:
    return float(view.meta("MOMENTUM").get("rsi", 50.0))


def macd_histogram(view: SnapshotView) -> float:
    return float(view.meta("MOMENTUM").get("macd_histogram", 0.0))


def side_of(regime: str) -> MarketSide:
    """Map a regime to a directional side (None for range/unknown)."""
    if regime == Regime.BULL.value:
        return MarketSide.LONG
    if regime == Regime.BEAR.value:
        return MarketSide.SHORT
    raise ValueError(f"{regime} is not a directional regime")


def accept_side(view: SnapshotView, side: MarketSide) -> bool:
    """True when the trend/momentum meta' align with the requested side."""
    dirn = trend_direction(view)
    if side == MarketSide.LONG:
        return dirn == "BULLISH" and momentum_state(view) == "POSITIVE"
    return dirn == "BEARISH" and momentum_state(view) == "NEGATIVE"


def base_candidate(
    view: SnapshotView, side: MarketSide, setup_type: SetupType
) -> SetupCandidate:
    """Build a candidate with the common fields already populated."""
    from ..candidate import SetupCandidate

    regime = Regime(view.regime)
    # Side must match the regime for directional setups.
    if side == MarketSide.LONG and regime != Regime.BULL:
        raise ValueError("LONG candidate requires BULL regime")
    if side == MarketSide.SHORT and regime != Regime.BEAR:
        raise ValueError("SHORT candidate requires BEAR regime")
    return SetupCandidate(
        symbol=view.symbol,
        timeframe=view.timeframe,
        timestamp=view.timestamp,
        setup_type=setup_type,
        side=side,
        regime=regime,
    )