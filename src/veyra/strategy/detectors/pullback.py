"""Pullback detector.

Hypothesis: a counter-trend retracement within an established trend, currently
in a meaningful support/interest area, with structure still confirming the
trend, is a pullback candidate (continuation) rather than a reversal.

Explicit rules (LONG shown; SHORT symmetric):

  P1. Regime directional and matches side.
  P2. Trend direction == side and strength >= min_trend_strength.
  P3. Momentum has turned but has NOT flipped against trend: for a LONG, RSI
      is below the fast-EMA reference (retrace) yet momentum state is not yet
      NEGATIVE against the trend — instead we require momentum to be starting
      to confirm (positive histogram slope) or simply not at a full reversal.
      We encode: momentum == POSITIVE (confirming) OR (momentum == NEUTRAL).
  P4. Structure must still support the trend:
        LONG -> structure is HH_HL (not LH_LL / no BOS that reversed).
        We treat LH_HL (tightening) as acceptable-for-development but not a
        confirmed continuation yet.
  P5. Price is "off its high": current close <= (recent high * (1 - min_retrace)).
      This separates a pullback (retraced) from a fresh breakout.
  P6. No active adverse break of structure (structure_action != "BREAK_OF_STRUCTURE_DOWN" for LONG).

Interest area: the retracement zone between the pullback low and the trend
reference (fast EMA proxy via pullback depth). Invalidation: a hard close
below the pullback low / last swing low.

Distinguishing pullback from reversal is made measurable: a reversal would
flip structure to LH_LL/LH_HL AND momentum to NEGATIVE at a new lower low — i.e.
rules P3/P4 fail.
"""

from __future__ import annotations

from typing import Optional

from ...domain import MarketSide, PriceZone, SetupType
from ..candidate import SetupCandidate
from ..snapshot_view import SnapshotView
from .base import SetupDetector
from . import rules


class PullbackDetector(SetupDetector):
    name = "pullback"

    def __init__(
        self,
        min_trend_strength: float = 55.0,
        min_retrace_pct: float = 0.02,   # must be pulled back at least 2% off high
        max_retrace_pct: float = 0.35,   # beyond this it's a change of character
    ) -> None:
        self._min_trend_strength = min_trend_strength
        self._min_retrace = min_retrace_pct
        self._max_retrace = max_retrace_pct

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

    def _reference_high(self, view: SnapshotView, side: MarketSide) -> Optional[float]:
        return rules.last_swing_high(view) if side == MarketSide.LONG else rules.last_swing_low(view)

    def _reference_low(self, view: SnapshotView, side: MarketSide) -> Optional[float]:
        return rules.last_swing_low(view) if side == MarketSide.LONG else rules.last_swing_high(view)

    def _conditions(self, view: SnapshotView, side: MarketSide) -> bool:
        # Established trend.
        if rules.trend_strength(view) < self._min_trend_strength:
            return False
        if rules.trend_direction(view) != ("BULLISH" if side == MarketSide.LONG else "BEARISH"):
            return False

        # Momentum must not have fully reversed against the trend.
        mom = rules.momentum_state(view)
        if side == MarketSide.LONG and mom == "NEGATIVE":
            return False
        if side == MarketSide.SHORT and mom == "POSITIVE":
            return False

        # Structure must still support the trend (not an active reversal).
        struct = rules.structure_state(view)
        if side == MarketSide.LONG and struct in ("LH_LL", "LOWER_HIGHS_LOWER_LOWS"):
            return False
        if side == MarketSide.SHORT and struct in ("HH_HL", "HIGHER_HIGHS_HIGHER_LOWS"):
            return False
        if rules.structure_action(view) in (
            "BREAK_OF_STRUCTURE_DOWN" if side == MarketSide.LONG else "BREAK_OF_STRUCTURE_UP",
        ):
            return False

        # Price must be retraced off the recent reference (pulled back).
        price, ref_high = self._current_price(view), self._reference_high(view, side)
        if price is None or ref_high is None or ref_high <= 0:
            return False
        retrace = self._retracement(view, side, ref_high)
        if not (self._min_retrace <= retrace <= self._max_retrace):
            return False
        return True

    def _retracement(self, view: SnapshotView, side: MarketSide, ref_high: float) -> float:
        price = self._current_price(view)
        ref_low = self._reference_low(view, side)
        if side == MarketSide.LONG:
            base = ref_low if ref_low else min(ref_high, price or ref_high)
            span = ref_high - base
            if span <= 0:
                return 0.0
            return (ref_high - price) / span
        # SHORT mirrored
        base = ref_low if ref_low else max(ref_high, price or ref_high)
        span = base - ref_high
        if span <= 0:
            return 0.0
        return (price - ref_high) / span

    def _current_price(self, view: SnapshotView) -> Optional[float]:
        return view.price()

    def _build(self, view: SnapshotView, side: MarketSide) -> SetupCandidate:
        cand = rules.base_candidate(view, side, SetupType.PULLBACK)
        price = self._current_price(view)
        ref_high = self._reference_high(view, side)
        last_low = rules.last_swing_low(view) if side == MarketSide.LONG else rules.last_swing_high(view)
        # Interest area: around the retracement low (support for LONG).
        if last_low is not None:
            cand.interest_area = PriceZone(last_low, max(last_low, price or last_low))
        if ref_high is not None:
            cand.targets = [PriceZone(ref_high, ref_high * 1.02)]
        cand.invalidation = self._invalidation(side, last_low)
        cand.evidence = {
            "trend_direction": rules.trend_direction(view),
            "structure": rules.structure_state(view),
            "momentum": rules.momentum_state(view),
            "retracement": self._retracement(view, side, ref_high) if ref_high else 0.0,
            "volatility": rules.volatility_state(view),
        }
        cand.reasoning = (
            f"Pullback in {cand.side.value}: {cand.evidence['structure']} structure "
            f"holding within {cand.evidence['trend_direction']} trend, "
            f"retracement {cand.evidence['retracement']*100:.1f}%."
        )
        return cand

    @staticmethod
    def _invalidation(side: MarketSide, last_low: Optional[float]) -> str:
        if last_low is None:
            return "Pullback low invalidated"
        return f"Close below {last_low:.6g} invalidates pullback"