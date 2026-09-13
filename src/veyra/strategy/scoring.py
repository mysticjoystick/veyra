"""Setup scoring.

Each component contributes a 0-100 alignment score from the Phase 2 snapshot,
and the overall setup score is the WeightedScoreAggregator's weighted average
Σ wᵢ·cᵢ / Σ wᵢ — a single canonical 0-100 scale shared by the live snapshot,
setups, backtest records, alerts, and paper trading.

This is a deterministic quality/ranking score, never a probability.
"""

from __future__ import annotations

from typing import Dict

from ..domain import AnalyticsComponent, MarketSide, Setup
from .aggregation import WeightedScoreAggregator
from .detectors import rules
from .snapshot_view import SnapshotView


class SetupScorer:
    def __init__(self, aggregator: WeightedScoreAggregator) -> None:
        self._agg = aggregator

    def apply(self, setup: Setup, view: SnapshotView) -> Setup:
        """Populate component scores + overall_score on the setup (in place)."""
        side = setup.side
        scores = self._component_scores(view, side)
        for name, value in scores.items():
            setup.set_component(name, value)
        setup.overall_score = self._agg.aggregate(
            {k.value: v for k, v in scores.items()}
        )
        return setup

    def _component_scores(
        self, view: SnapshotView, side: MarketSide
    ) -> Dict[AnalyticsComponent, int]:
        scores: Dict[AnalyticsComponent, int] = {}

        trend = view.meta("TREND")
        scores[AnalyticsComponent.TREND] = self._trend(view)

        struct = view.meta("STRUCTURE")
        scores[AnalyticsComponent.STRUCTURE] = self._structure(view, side)

        scores[AnalyticsComponent.MOMENTUM] = self._momentum(view, side)
        scores[AnalyticsComponent.VOLUME] = self._volume(view, side)
        scores[AnalyticsComponent.VOLATILITY] = self._volatility(view, side)

        # Pullback-relevant score: reward structure depth consistent with a
        # healthy pullback (or the pullback detector's retracement evidence).
        scores[AnalyticsComponent.PULLBACK] = self._pullback(view, side)
        return scores

    # --- Component helpers (each 0-100) ----------------------------------

    def _trend(self, view: SnapshotView) -> int:
        return view.score("TREND")

    def _structure(self, view: SnapshotView, side: MarketSide) -> int:
        value = view.score("STRUCTURE")
        struct = view.meta("STRUCTURE")
        # Penalise structure that opposes the requested side.
        opposed = (
            struct.get("structure") in ("LH_LL",)
            if side == MarketSide.LONG
            else struct.get("structure") in ("HH_HL",)
        )
        if opposed:
            value = max(0, value - 40)
        return value

    def _momentum(self, view: SnapshotView, side: MarketSide) -> int:
        value = view.score("MOMENTUM")
        mom = view.meta("MOMENTUM")
        # Strongly penalise momentum against the side.
        opposed = (
            mom.get("momentum") == "NEGATIVE"
            if side == MarketSide.LONG
            else mom.get("momentum") == "POSITIVE"
        )
        if opposed:
            value = max(0, value - 50)
        return value

    def _volume(self, view: SnapshotView, side: MarketSide) -> int:
        value = view.score("VOLUME")
        volume = view.meta("VOLUME")
        # Confirmation on the side's direction is a bonus; absence is neutral.
        if volume.get("price_volume_confirmation"):
            return min(100, value + 15)
        return value

    def _volatility(self, view: SnapshotView, side: MarketSide) -> int:
        vol = view.meta("VOLATILITY")
        state = vol.get("volatility_state", "UNKNOWN")
        # For most setups, controlled volatility is desirable (score high).
        inverse = {
            "EXTREME": 15,
            "HIGH": 40,
            "NORMAL": 75,
            "LOW": 90,
            "UNKNOWN": 0,
        }
        return max(0, min(100, inverse.get(state, 0)))

    def _pullback(self, view: SnapshotView, side: MarketSide) -> int:
        # Pullback component reflects a healthy retracement depth. We rely on
        # structure/momentum being aligned with the trend (rewarded above);
        # here we reward a moderate retracement when present.
        struct = rules.structure_state(view)
        mom = rules.momentum_state(view)
        aligned = (
            struct in ("HH_HL",) and mom == "POSITIVE"
            if side == MarketSide.LONG
            else struct in ("LH_LL",) and mom == "NEGATIVE"
        )
        return 85 if aligned else 40