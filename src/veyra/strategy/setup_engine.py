"""Setup detection & lifecycle engine.

The concrete Phase 3 SetupEngine. It:

  * gates on data quality (no high-confidence claims from unreliable input),
  * runs the configured detectors over a MarketSnapshot and promotes each
    candidate to a deterministic Setup (state = DETECTED),
  * scores each setup via WeightedScoreAggregator (0-100 alignment),
  * advances an existing setup's lifecycle on subsequent snapshots
    (developing -> qualified -> triggered | invalidated | expired),
  * persists setups + append-only events though a SetupRepository.

It never emits signals, never claims profitability, and never uses AI.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..config import Settings
from ..domain import (
    AnalyticsComponent,
    SetupState,
)
from ..domain.setup import Setup as DomainSetup
from ..domain.snapshot import MarketSnapshot
from .aggregation import WeightedScoreAggregator
from .candidate import SetupCandidate
from .detectors import SetupDetector
from .lifecycle import transition
from .scoring import SetupScorer
from .snapshot_view import SnapshotView


class SetupEngine:
    name = "setup"

    def __init__(
        self,
        settings: Settings,
        detectors: Optional[List[SetupDetector]] = None,
        aggregator: Optional[WeightedScoreAggregator] = None,
        scorer: Optional[SetupScorer] = None,
    ) -> None:
        self._settings = settings
        self._detectors = detectors if detectors is not None else self._build_detectors()
        self._aggregator = aggregator or self._build_aggregator(settings)
        self._scorer = scorer or SetupScorer(self._aggregator)
        self._max_lifetime_bars = settings.setup_max_lifetime_bars

    def _build_detectors(self) -> List[SetupDetector]:
        s = self._settings
        from .detectors.breakout import BreakoutDetector
        from .detectors.breakout_retest import BreakoutRetestDetector
        from .detectors.pullback import PullbackDetector
        from .detectors.range_rejection import RangeRejectionDetector
        from .detectors.trend_continuation import TrendContinuationDetector

        return [
            TrendContinuationDetector(
                min_trend_strength=s.setup_min_trend_strength,
                min_structure_score=s.setup_min_structure_score,
            ),
            PullbackDetector(
                min_trend_strength=s.setup_min_trend_strength,
                min_retrace_pct=s.setup_pullback_min_retrace,
                max_retrace_pct=s.setup_pullback_max_retrace,
            ),
            BreakoutDetector(breakout_distance_pct=s.setup_breakout_distance_pct),
            BreakoutRetestDetector(
                retest_tolerance_pct=s.setup_retest_tolerance_pct,
                lookback_max_bars=s.setup_retest_max_bars,
            ),
            RangeRejectionDetector(
                boundary_tolerance_pct=s.setup_range_boundary_tolerance_pct
            ),
        ]

    @staticmethod
    def _build_aggregator(settings: Settings) -> WeightedScoreAggregator:
        return WeightedScoreAggregator(
            {
                AnalyticsComponent.TREND.value: settings.weight_trend,
                AnalyticsComponent.STRUCTURE.value: settings.weight_structure,
                AnalyticsComponent.PULLBACK.value: settings.weight_pullback,
                AnalyticsComponent.MOMENTUM.value: settings.weight_momentum,
                AnalyticsComponent.VOLUME.value: settings.weight_volume,
                AnalyticsComponent.VOLATILITY.value: settings.weight_volatility,
            }
        )

    # -- Detection ----------------------------------------------------------

    def detect(self, snapshot: MarketSnapshot) -> List[DomainSetup]:
        """Return zero or more deterministic setups for a snapshot.

        Regime gate: directional regimes (BULL/BEAR) run the trend-aware
        detectors; RANGE runs only the range-rejection detector (fades at the
        range edge — mean-reversion, never trend chasing). HIGH_VOLATILITY and
        UNKNOWN regimes produce zero setups.
        """
        view = SnapshotView(snapshot)
        if not self._usable_quality(view):
            return []
        regime = view.regime
        if regime == "RANGE":
            detectors = [d for d in self._detectors if d.name == "range_rejection"]
        elif regime in ("BULL", "BEAR"):
            detectors = [d for d in self._detectors if d.name != "range_rejection"]
        else:
            detectors = []
        setups: List[DomainSetup] = []
        for detector in detectors:
            cand = detector.detect(view)
            if cand is not None:
                setups.append(self._promote(view, cand))
        return setups

    def _usable_quality(self, view: SnapshotView) -> bool:
        state = view.data_quality_state
        return state not in ("INSUFFICIENT_DATA", "INVALID", "UNKNOWN")

    def _promote(self, view: SnapshotView, cand: SetupCandidate) -> DomainSetup:
        setup = DomainSetup(
            symbol=cand.symbol,
            timeframe=cand.timeframe,
            timestamp=cand.timestamp,
            setup_type=cand.setup_type,
            side=cand.side,
            regime=cand.regime,
            interest_area=cand.interest_area,
            invalidation=cand.invalidation,
            targets=cand.targets,
            expiry_condition=cand.expiry_condition,
            reasoning=cand.reasoning,
            state=SetupState.DETECTED,
        )
        setup.evidence = dict(cand.evidence)
        # Attach ATR for ATR-based exit logic in the simulator.
        atr = view.meta("VOLATILITY").get("atr")
        if atr is not None:
            setup.evidence["atr"] = atr
        self._scorer.apply(setup, view)
        return setup

    # -- Scoring ------------------------------------------------------------

    def score(self, setup: DomainSetup, snapshot: MarketSnapshot) -> DomainSetup:
        view = SnapshotView(snapshot)
        return self._scorer.apply(setup, view)

    # -- Lifecycle ----------------------------------------------------------

    def advance(self, setup: DomainSetup, snapshot: MarketSnapshot) -> Optional[SetupState]:
        """Evaluate a live setup against a later snapshot.

        Returns the new state if it changed, else None. Terminal states are
        left untouched. Deterministic rules only.
        """
        view = SnapshotView(snapshot)
        current = setup.state
        if lifecycle_is_terminal(current):
            return None

        if self._expired(setup, view):
            return self._go(setup, SetupState.EXPIRED)

        if self._invalidated(setup, view):
            return self._go(setup, SetupState.INVALIDATED)

        if self._confirms(setup, view):
            if current == SetupState.DETECTED:
                return self._go(setup, SetupState.DEVELOPING)
            if current == SetupState.DEVELOPING:
                return self._go(setup, SetupState.QUALIFIED)
        return None

    def _go(self, setup: DomainSetup, new: SetupState) -> SetupState:
        transition(setup.state, new)
        setup.state = new
        return new

    def _invalidated(self, setup: DomainSetup, view: SnapshotView) -> bool:
        struct = view.meta("STRUCTURE").get("structure", "UNKNOWN")
        momentum = view.meta("MOMENTUM").get("momentum", "UNKNOWN")
        long_side = setup.side.value == "LONG"
        # Structure now opposing the original side invalidates.
        if long_side and struct in ("LH_LL", "LOWER_HIGHS_LOWER_LOWS"):
            return True
        if not long_side and struct in ("HH_HL", "HIGHER_HIGHS_HIGHER_LOWS"):
            return True
        # If a break-of-structure now opposes the side, invalidate.
        action = view.meta("STRUCTURE").get("action", "NONE")
        if long_side and action == "BREAK_OF_STRUCTURE_DOWN":
            return True
        if not long_side and action == "BREAK_OF_STRUCTURE_UP":
            return True
        return False

    def _confirms(self, setup: DomainSetup, view: SnapshotView) -> bool:
        # A setup develops/qualifies while its side still has support.
        momentum = view.meta("MOMENTUM").get("momentum", "UNKNOWN")
        long_side = setup.side.value == "LONG"
        return (momentum == "POSITIVE") if long_side else (momentum == "NEGATIVE")

    def _bars_since(self, det_ts: int, now_ts: int, timeframe: str) -> int:
        interval = self._settings.timeframe_interval_seconds.get(timeframe, 14400)
        if now_ts <= det_ts or interval <= 0:
            return 0
        return max(0, int((now_ts - det_ts) // interval))

    def _expired(self, setup: DomainSetup, view: SnapshotView) -> bool:
        # Age-based expiry: bars since detection beyond maximum lifetime.
        bars = self._bars_since(setup.timestamp, view.timestamp, view.timeframe)
        if bars > self._max_lifetime_bars:
            return True
        # Regime transition expires the setup.
        if view.regime != setup.regime.value and view.regime not in (
            "UNKNOWN", "INSUFFICIENT_DATA"
        ):
            return True
        return False


def lifecycle_is_terminal(state: SetupState) -> bool:
    from .lifecycle import is_terminal

    return is_terminal(state)


# Backwards-compatible re-export for earlier phases/tests and the pipeline.
from .aggregation import WeightedScoreAggregator  # noqa: E402, F401

# Convenience alias for callers that reference the old contract name.
SetupEngineContract = SetupEngine  # noqa: E305