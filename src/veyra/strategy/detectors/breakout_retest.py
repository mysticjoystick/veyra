"""Breakout-retest detector.

Hypothesis: after a breakout, price returning to the just-broken level
(retest) and holding above it confirms the breakout; a retest that fails
(re-close through the level) invalidates it.

Level definition: the last confirmed swing high (LONG) that was broken. On a
single snapshot we detect the *retest state*: price is above the broken level
(after the break) but within a configurable distance of it — i.e. it has come
back toward the level rather than running far from it.

Explicit rules (LONG shown; SHORT symmetric):

  P1. Regime directional and matches side.
  P2. The broken level exists (last swing high for LONG).
  P3. Price is above the level (still "broken") ...
  P4. ...yet within retest_tolerance of it (has retraced back to it).
  P5. Price has NOT re-closed below the level (not a failed break).

The engine's lifecycle pushes this through RETEST -> CONFIRMATION -> QUALIFIED;
a re-close through the level moves it to INVALIDATED (failed retest).
"""

from __future__ import annotations

from typing import Optional

from ...domain import MarketSide, PriceZone, SetupType
from ..candidate import SetupCandidate
from ..snapshot_view import SnapshotView
from .base import SetupDetector
from . import rules


class BreakoutRetestDetector(SetupDetector):
    name = "breakout_retest"

    def __init__(
        self,
        retest_tolerance_pct: float = 0.02,   # within 2% of the level
        lookback_max_bars: int = 20,          # reserved; handled by engine later
    ) -> None:
        self._tol = retest_tolerance_pct
        self._lookback = lookback_max_bars

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
        if side == MarketSide.LONG:
            # Still above the level (retest from above).
            if price < level:
                return False
            # Close enough to the level to count as "coming back to it".
            distance = (price - level) / level
            if distance > self._tol:
                return False
        else:
            if price > level:
                return False
            distance = (level - price) / level
            if distance > self._tol:
                return False
        return True

    def _build(self, view: SnapshotView, side: MarketSide) -> SetupCandidate:
        cand = rules.base_candidate(view, side, SetupType.BREAKOUT_RETEST)
        level = self._level(view, side)
        if level is not None:
            cand.interest_area = PriceZone(level, level * 1.01)
            cand.targets = [PriceZone(level * 1.03, level * 1.06)]
        cand.invalidation = (
            f"Re-close below broken level {level:.6g} = failed retest"
            if level is not None else "Failed retest"
        )
        cand.expiry_condition = f"{self._lookback} bars max for confirmation"
        cand.evidence = {
            "broken_level": level,
            "retest_tolerance_pct": self._tol,
            "regime": view.regime,
            "volume_state": rules.volume_state(view),
            "momentum": rules.momentum_state(view),
        }
        cand.reasoning = (
            f"{cand.side.value} retest of broken level {level:.6g} "
            f"within {self._tol*100:.1f}% tolerance."
        )
        return cand