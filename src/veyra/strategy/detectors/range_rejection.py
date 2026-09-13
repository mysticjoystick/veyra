"""Range-rejection detector.

Hypothesis: in an established range, price interacting with a defined range
boundary and producing rejection evidence is a candidate for range rejection
(mean-reversion toward the opposite boundary).

Range definition (deterministic): the engine only proposes a rejection when
the Phase 2 regime is RANGE and structure is non-trending (LH_HL = tightening,
or no HH_HL/LH_LL). The boundary is the most recent confirmed swing high
(resistance) or swing low (support).

Rules (SHORT at resistance shown; LONG at support symmetric):

  P1. Regime must be RANGE (no directional bias) and data quality is fine.
  P2. Structure is not trending (state in {LH_HL, NEUTRAL}).
  P3. A boundary exists: a confirmed swing high (resistance) for a SHORT.
  P4. Price is at the boundary: close within range_boundary_tolerance of it.
  P5. Rejection evidence: momentum is not strong, and volatility is bounced
      (state != LOW) OR a break-of-structure DOWN is the nearest signal.
      Concretely we require momentum != POSITIVE (no momentum up into
      resistance) and the structure action is not BREAK_OF_STRUCTURE_UP.

Interest area: the boundary rejection zone. Invalidation: price clears the
boundary (breakout) by more than tolerance.
"""

from __future__ import annotations

from typing import Optional

from ...domain import MarketSide, PriceZone, Regime, SetupType
from ..candidate import SetupCandidate
from ..snapshot_view import SnapshotView
from .base import SetupDetector
from . import rules


class RangeRejectionDetector(SetupDetector):
    name = "range_rejection"

    def __init__(self, boundary_tolerance_pct: float = 0.015) -> None:
        self._tol = boundary_tolerance_pct

    def detect(self, view: SnapshotView) -> Optional[SetupCandidate]:
        if view.regime != Regime.RANGE.value:
            return None
        if not self._non_trending(view):
            return None

        # Choose the side: SHORT if price at/above resistance, LONG if at support.
        price = view.meta("STRUCTURE").get("value", None)
        resist = rules.last_swing_high(view)
        support = rules.last_swing_low(view)
        if price is None:
            return None

        if resist is not None and self._at_boundary(price, resist):
            side = MarketSide.SHORT
            bound = resist
        elif support is not None and self._at_boundary(price, support):
            side = MarketSide.LONG
            bound = support
        else:
            return None

        if not self._rejection_evidence(view, side):
            return None
        return self._build(view, side, bound)

    def _at_boundary(self, price: float, boundary: float) -> bool:
        return abs(price - boundary) / boundary <= self._tol

    def _non_trending(self, view: SnapshotView) -> bool:
        struct = rules.structure_state(view)
        return struct in ("LH_HL", "NEUTRAL", "LOWER_HIGHS_HIGHER_LOWS")

    def _rejection_evidence(self, view: SnapshotView, side: MarketSide) -> bool:
        mom = rules.momentum_state(view)
        action = rules.structure_action(view)
        if side == MarketSide.SHORT:
            if mom == "POSITIVE":
                return False
            if action == "BREAK_OF_STRUCTURE_UP":
                return False
        else:
            if mom == "NEGATIVE":
                return False
            if action == "BREAK_OF_STRUCTURE_DOWN":
                return False
        # Rejection is more credible with at least some volatility to bounce off.
        if rules.volatility_state(view) == "LOW":
            return False
        return True

    def _build(self, view: SnapshotView, side: MarketSide, boundary: float) -> SetupCandidate:
        cand = SetupCandidate(
            symbol=view.symbol,
            timeframe=view.timeframe,
            timestamp=view.timestamp,
            setup_type=SetupType.RANGE_REJECTION,
            side=side,
            regime=Regime(view.regime),
        )
        cand.interest_area = PriceZone(boundary, boundary * 1.01)
        cand.invalidation = (
            f"Close through boundary {boundary:.6g} = breakout, rejection invalid"
        )
        # Target: the opposite boundary is the logical mean-reversion objective.
        opp = (
            rules.last_swing_low(view)
            if side == MarketSide.SHORT
            else rules.last_swing_high(view)
        )
        if opp is not None:
            cand.targets = [PriceZone(min(boundary, opp), max(boundary, opp))]
        cand.evidence = {
            "regime": view.regime,
            "structure": rules.structure_state(view),
            "boundary": boundary,
            "momentum": rules.momentum_state(view),
            "volatility": rules.volatility_state(view),
            "boundary_tolerance_pct": self._tol,
        }
        cand.reasoning = (
            f"{side.value} rejection at range boundary "
            f"{boundary:.6g} (non-trending structure, "
            f"{cand.evidence['momentum']} momentum)."
        )
        return cand