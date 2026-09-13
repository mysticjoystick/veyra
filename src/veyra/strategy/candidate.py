"""Detection candidate model.

A SetupCandidate is the intermediate, unvalidated output of a detector for a
single snapshot. It carries the raw evidence a detector used so the scorer and
future Analyst layer can explain the decision without recomputing indicators.
Once accepted it is promoted to a full domain Setup (state=DETECTED).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from ..domain import MarketSide, PriceZone, Regime, SetupType


@dataclass
class SetupCandidate:
    symbol: str
    timeframe: str
    timestamp: int                      # epoch seconds, UTC (snapshot time)
    setup_type: SetupType
    side: MarketSide
    regime: Regime

    interest_area: Optional[PriceZone] = None
    invalidation: Optional[str] = None
    targets: list[PriceZone] = field(default_factory=list)

    expiry_condition: Optional[str] = None

    # Human + machine-readable narrative of which rules fired.
    reasoning: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "timestamp": self.timestamp,
            "setup_type": self.setup_type.value,
            "side": self.side.value,
            "regime": self.regime.value,
            "interest_area": (
                {"low": self.interest_area.low, "high": self.interest_area.high}
                if self.interest_area
                else None
            ),
            "invalidation": self.invalidation,
            "targets": [
                {"low": z.low, "high": z.high} for z in self.targets
            ],
            "expiry_condition": self.expiry_condition,
            "reasoning": self.reasoning,
            "evidence": self.evidence,
        }