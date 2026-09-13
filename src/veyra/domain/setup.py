"""Setup domain models.

A qualified Veyra setup captures a full, machine-testable opportunity:
regime, scores, interest area, invalidation, targets, status, and expiry.

All conditions described here must be expressible in measurable terms for
backtesting and paper trading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import (
    AnalyticsComponent,
    MarketSide,
    Regime,
    SetupState,
    SetupType,
)


@dataclass(frozen=True)
class PriceZone:
    low: float
    high: float

    def contains(self, price: float) -> bool:
        return self.low <= price <= self.high


@dataclass
class Setup:
    symbol: str
    timeframe: str
    timestamp: int  # epoch seconds, UTC (snapshot time)
    setup_type: SetupType
    side: MarketSide

    regime: Regime = Regime.UNKNOWN

    overall_score: int = 0
    scores: Dict[AnalyticsComponent, int] = field(default_factory=dict)

    interest_area: Optional[PriceZone] = None
    invalidation: Optional[str] = None
    targets: List[PriceZone] = field(default_factory=list)

    state: SetupState = SetupState.DETECTED
    reasoning: str = ""
    expiry_condition: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)

    def set_component(self, name: AnalyticsComponent, score: int) -> None:
        self.scores[name] = max(0, min(100, score))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "timestamp": self.timestamp,
            "setup_type": self.setup_type.value,
            "side": self.side.value,
            "regime": self.regime.value,
            "overall_score": self.overall_score,
            "scores": {k.value: v for k, v in self.scores.items()},
            "interest_area": (
                {"low": self.interest_area.low, "high": self.interest_area.high}
                if self.interest_area
                else None
            ),
            "invalidation": self.invalidation,
            "targets": [{"low": z.low, "high": z.high} for z in self.targets],
            "state": self.state.value,
            "reasoning": self.reasoning,
            "expiry_condition": self.expiry_condition,
            "evidence": self.evidence,
        }
