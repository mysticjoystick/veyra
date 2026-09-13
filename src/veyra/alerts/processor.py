"""Alert processor.

Turns a deterministic backtest/paper replay result into a list of selective
setup alerts. It is a pure function of its inputs:

  * the SimulationResult (already deterministic, same decisions every run)
  * an AlertPolicy (selectivity: min score, window cap)

Because the underlying replay is frozen and the policy is explicit, two runs
over identical candles always yield identical alerts. The processor never
executes anything, never contacts an exchange, and never writes state — it only
emits notices for a human to review.

Which setups are alertable: a setup that reached QUALIFIED (or resolved while
qualified) with a normalised score above the policy threshold. Detections alone
are never alerted.
"""

from __future__ import annotations

from typing import List, Optional

from ..backtest import SimulationResult
from .models import AlertLevel, SetupAlert
from .policy import AlertPolicy


class AlertProcessor:
    def __init__(self, policy: Optional[AlertPolicy] = None) -> None:
        self._policy = policy or AlertPolicy()

    def run(self, result: SimulationResult) -> List[SetupAlert]:
        """Filter the replay's setups into ordered, capped alerts."""
        candidates = sorted(
            (s for s in result.setups if self._policy.allows(s.outcome, s.score_normalized)),
            key=lambda s: (s.detection_ts, -s.score_normalized),
        )
        alerts: List[SetupAlert] = []
        for s in candidates:
            raw = self._build(s)
            if self._within_budget([a.timestamp for a in alerts], raw.timestamp):
                alerts.append(raw)
        return alerts

    def _within_budget(self, prior_ts: List[int], ts: int) -> bool:
        policy = self._policy
        if policy.max_per_window <= 0:
            return True
        # Count how many alerts fall inside the rolling window ending at `ts`.
        recent = [t for t in prior_ts if ts - t < policy.window_seconds]
        return len(recent) < policy.max_per_window

    def _build(self, s) -> SetupAlert:
        # s is a backtest.models.SetupRecord.
        level = AlertLevel.for_score(s.score_normalized)
        return SetupAlert(
            alert_id=f"{s.timeframe}|{s.key}",
            symbol=s.symbol,
            timeframe=s.timeframe,
            setup_key=s.key,
            setup_type=s.setup_type,
            side=s.side,
            regime=s.regime,
            timestamp=s.detection_ts,
            overall_score=s.score,
            score_normalized=s.score_normalized,
            level=level.name,
            interest_area_low=s.interest_area_low,
            interest_area_high=s.interest_area_high,
            invalidation=s.invalidation,
            reasoning=f"{s.setup_type} {s.side} {s.regime} score={s.score_normalized}",
            properties={
                "_state": s.final_state,
                "_outcome": s.outcome,
            },
        )