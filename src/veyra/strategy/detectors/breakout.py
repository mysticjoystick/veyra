"""Breakout detector.

Hypothesis: price closing through a deterministic resistance level (the most
recent confirmed swing high) represents a breakout candidate, with volume and
volatility captured as confirmation *evidence* (not assumed to improve the
setup — that is a Phase 4 question).

Level definition (deterministic): the last confirmed swing high (LONG) held in
the snapshot's structure engine — no vague "strong resistance".

Explicit rules (LONG shown; SHORT symmetric):

  P1. Regime directional and matches side.
  P2. There is a defined level: a last swing high exists for LONG.
  P3. Price (close) has crossed above that level: close > level (LONG).
  P4. Structure engine reports a matching break: action == BREAK_OF_STRUCTURE_UP (LONG).
  P5. Momentum supports the direction (POSITIVE for LONG) OR is NEUTRAL.

Confirmations (evidence only, not gates):
  - volume_state == EXPANDING / relative_volume
  - price_volume_confirmed
  - volatility not extreme

Interest area: the just-broken level (retest zone). Invalidation: price
re-closes back under the broken level (failed breakout) by more than tolerance.
"""

from __future__ import annotations

from typing import Optional

from ...domain import MarketSide, PriceZone, SetupType
from ..candidate import SetupCandidate
from ..snapshot_view import SnapshotView
from .base import SetupDetector
from . import rules


class BreakoutDetector(SetupDetector):
    name = "breakout"

    def __init__(self, breakout_distance_pct: float = 0.001) -> None:
        # Minimum clear of the level beyond noise (0.1% by default).
        self._dist = breakout_distance_pct

    def detect(self, view: SnapshotView) -> Optional[SetupCandidate]:
        side = self._side(view)
        if side is None:
            return None
        if not self._conditions(view, side):
            return None
        return self._build(view, side)

    def _side(self, view: SnapshotView) -> Optional[MarketSide]:
        try:
            return rules.side_of(view.regime)
        except ValueError:
            return None

    def _level(self, view: SnapshotView, side: MarketSide) -> Optional[float]:
        return rules.last_swing_high(view) if side == MarketSide.LONG else rules.last_swing_low(view)

    def _conditions(self, view: SnapshotView, side: MarketSide) -> bool:
        level = self._level(view, side)
        price = view.price()
        if level is None or price is None:
            return False
        mom = rules.momentum_state(view)
        if side == MarketSide.LONG:
            if price <= level * (1.0 + self._dist):
                return False
            if rules.structure_action(view) != "BREAK_OF_STRUCTURE_UP":
                return False
            if mom == "NEGATIVE":
                return False
        else:
            if price >= level * (1.0 - self._dist):
                return False
            if rules.structure_action(view) != "BREAK_OF_STRUCTURE_DOWN":
                return False
            if mom == "POSITIVE":
                return False
        # Volume gate: breakouts require expanding or normal volume.
        vol_state = rules.volume_state(view)
        if vol_state == "CONTRACTION":
            return False
        return True

    def _build(self, view: SnapshotView, side: MarketSide) -> SetupCandidate:
        cand = rules.base_candidate(view, side, SetupType.BREAKOUT)
        level = self._level(view, side)
        price = view.meta("STRUCTURE").get("value", None)
        if level is not None:
            # Interest area = the broken level itself (retest zone).
            cand.interest_area = PriceZone(level, level * 1.01)
            # Target = measured move above the level (deterministic).
            if price is not None and price > 0:
                move = abs(price - level)
                cand.targets = [PriceZone(price + move, price + 2 * move)]
        cand.invalidation = (
            f"Re-close below broken level {level:.6g}" if level is not None else "Breakout failed"
        )
        cand.evidence = {
            "broken_level": level,
            "volume_state": rules.volume_state(view),
            "relative_volume": rules.relative_volume(view),
            "price_volume_confirmed": rules.price_volume_confirmed(view),
            "volatility": rules.volatility_state(view),
        }
        cand.reasoning = (
            f"{cand.side.value} breakout above {level:.6g} with "
            f"{cand.evidence['volume_state']} volume"
            f"({cand.evidence['relative_volume']:.2f}x)."
        )
        return cand