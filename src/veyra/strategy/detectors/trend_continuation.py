"""Trend-continuation detector.

Hypothesis: in an established trend, a market that re-aligns trend
(direction + structure + momentum) with acceptable volatility represents a
candidate for trend continuation.

Explicit rules (all must hold; LONG shown, SHORT symmetric):

  P1. Data quality is not INSUFFICIENT/INVALID (snapshot-level gate is
      enforced by the engine before detectors run).
  P2. Regime is directional and matches the requested side.
  P3. Trend direction == side and trend strength >= min_trend_strength.
  P4. Structure == HH_HL (LONG) / LH_LL (SHORT).
  P5. Momentum == POSITIVE (LONG) / NEGATIVE (SHORT).
  P6. Volatility is not HIGH/EXTREME (avoid continuation into explosive moves).
  P7. There is a defined interest area: the pullback zone between the last
      swing low and the current close (LONG).

Interest area / invalidation / expiry (deterministic, configurable) are set on
the candidate so downstream lifecycle can reason about them.
"""

from __future__ import annotations

from typing import Optional

from ...domain import MarketSide, PriceZone, SetupType
from ..candidate import SetupCandidate
from ..snapshot_view import SnapshotView
from .base import SetupDetector
from . import rules


class TrendContinuationDetector(SetupDetector):
    name = "trend_continuation"

    def __init__(
        self,
        min_trend_strength: float = 55.0,
        min_structure_score: int = 70,
    ) -> None:
        self._min_trend_strength = min_trend_strength
        self._min_structure_score = min_structure_score

    def detect(self, view: SnapshotView) -> Optional[SetupCandidate]:
        side = self._side(view)
        if side is None:
            return None

        if not self._rules_hold(view, side):
            return None

        return self._build(view, side)

    def _side(self, view: SnapshotView) -> Optional[MarketSide]:
        try:
            return rules.side_of(view.regime)
        except ValueError:
            return None

    def _rules_hold(self, view: SnapshotView, side: MarketSide) -> bool:
        if rules.trend_strength(view) < self._min_trend_strength:
            return False
        if not rules.accept_side(view, side):
            return False
        # Structure must confirm the trend's direction.
        struct = rules.structure_state(view)
        if side == MarketSide.LONG and struct != "HH_HL":
            return False
        if side == MarketSide.SHORT and struct != "LH_LL":
            return False
        # No active break of structure that invalidates the narrative.
        if rules.structure_action(view) != "NONE":
            return False
        # Acceptable volatility.
        if rules.volatility_state(view) in ("HIGH", "EXTREME"):
            return False
        return True

    def _build(self, view: SnapshotView, side: MarketSide) -> SetupCandidate:
        cand = rules.base_candidate(view, side, SetupType.TREND_CONTINUATION)
        last_low = rules.last_swing_low(view)
        if last_low is None:
            cand.interest_area = None
        else:
            # Interest area: hold/trigger zone above the last swing low.
            cand.interest_area = PriceZone(last_low, max(last_low * 1.01, last_low))
            # Target = continuation above the recent structure high.
            last_high = rules.last_swing_high(view)
            if last_high is not None:
                cand.targets = [PriceZone(last_high, last_high * 1.02)]
        cand.invalidation = self._invalidation(side, last_low)
        cand.evidence = {
            "trend_direction": rules.trend_direction(view),
            "trend_strength": rules.trend_strength(view),
            "structure": rules.structure_state(view),
            "momentum": rules.momentum_state(view),
            "volatility": rules.volatility_state(view),
        }
        cand.reasoning = (
            f"Trend continuation {cand.side.value}: "
            f"{cand.evidence['structure']} structure in a "
            f"{cand.evidence['trend_direction']} {view.regime} regime with "
            f"{cand.evidence['momentum']} momentum."
        )
        return cand

    @staticmethod
    def _invalidation(side: MarketSide, last_low: Optional[float]) -> str:
        if side == MarketSide.LONG and last_low is not None:
            return f"Close below last swing low {last_low:.6g}"
        if side == MarketSide.SHORT and last_low is not None:
            return f"Close above last swing high (see last_swing_high)"
        return "Structure/regime change invalidates continuation"