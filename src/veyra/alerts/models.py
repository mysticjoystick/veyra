"""Alert domain models.

A setup alert is a selective, machine-generated notice emitted when a qualified
setup meets the alert policy. It is a *notice* only — it never places orders,
never touches real money, and never executes. Alerts carry the genuine,
normalised alignment score plus the deterministic reasoning so a human can
decide whether the opportunity warrants attention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class AlertLevel:
    """Clarity band for an alert, derived only from the 0-100 alignment score.

    This is the ONE ladder for the whole system. The score is canonical 0-100
    (a weighted average of component alignment), and the same band function is
    used by the dashboard, alerts, backtest and paper trading — a setup shown
    as EMERGENT on the dashboard is trade-eligible at the same number.

    The names describe *how aligned the setup currently is* — not how
    profitable it will be. No alert promises an edge; higher clarity only
    means more components currently point the same way. Ladder
    (highest clarity first):

      CONVERGENT  >= 75 : every lens agrees; the picture is sharp.
      DIRECTIONAL >= 55 : a clear one-way bias has formed.
      EMERGENT    >= 35 : a setup is taking shape.
      SCANNED        0  : merely observed, not yet a setup.
    """

    name: str
    min_score: int
    tagline: str

    @classmethod
    def for_score(cls, score: int) -> "AlertLevel":
        # Thresholds are static and documented; they are a reporting aid, NOT a
        # tradeable signal and NOT part of the frozen execution assumptions.
        for band in cls.bands():
            if score >= band.min_score:
                return band
        return cls("SCANNED", 0, "observed, not a full setup")

    @classmethod
    def bands(cls) -> List["AlertLevel"]:
        # Ordered highest-threshold first so for_score stops at the first hit.
        return [
            cls("CONVERGENT", 75, "all lenses aligned"),
            cls("DIRECTIONAL", 55, "clear one-way bias"),
            cls("EMERGENT", 35, "setup taking shape"),
            cls("SCANNED", 0, "observed, not a full setup"),
        ]


@dataclass(frozen=True)
class SetupAlert:
    """One selective alert about a qualified setup."""

    alert_id: str
    symbol: str
    timeframe: str
    setup_key: str
    setup_type: str
    side: str
    regime: str
    timestamp: int  # epoch seconds, UTC (detection time)
    overall_score: int
    score_normalized: int
    level: str
    interest_area_low: Optional[float]
    interest_area_high: Optional[float]
    invalidation: str
    reasoning: str
    properties: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "alert_id": self.alert_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "setup_key": self.setup_key,
            "setup_type": self.setup_type,
            "side": self.side,
            "regime": self.regime,
            "timestamp": self.timestamp,
            "overall_score": self.overall_score,
            "score_normalized": self.score_normalized,
            "level": self.level,
            "interest_area_low": self.interest_area_low,
            "interest_area_high": self.interest_area_high,
            "invalidation": self.invalidation,
            "reasoning": self.reasoning,
            "properties": self.properties,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "SetupAlert":
        return cls(
            alert_id=str(data["alert_id"]),
            symbol=str(data["symbol"]),
            timeframe=str(data["timeframe"]),
            setup_key=str(data["setup_key"]),
            setup_type=str(data["setup_type"]),
            side=str(data["side"]),
            regime=str(data["regime"]),
            timestamp=int(data["timestamp"]),
            overall_score=int(data["overall_score"]),
            score_normalized=int(data["score_normalized"]),
            level=str(data["level"]),
            interest_area_low=(
                float(data["interest_area_low"])
                if data.get("interest_area_low") is not None
                else None
            ),
            interest_area_high=(
                float(data["interest_area_high"])
                if data.get("interest_area_high") is not None
                else None
            ),
            invalidation=str(data["invalidation"]),
            reasoning=str(data["reasoning"]),
            properties=dict(data.get("properties", {})),
        )