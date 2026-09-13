"""Trading cost model for honest, cost-adjusted forward returns (Step 2).

Raw 24h forward returns ignore the friction of actually trading. Real fills
cost money, and many backtests that look profitable on gross returns are not
after costs. This module provides a small, configurable cost model that Step 2
uses to re-measure every 24h projection net of fees and slippage.

The model is deliberately simple and transparent:

    round-trip cost = 2 * (fee_per_side + slippage_per_side)

Default assumptions (Binance-style, conservative enough to be honest):
    fee_per_side       0.10%  (taker)
    slippage_per_side  0.05%  (entry/exit crossing the spread)

Any of these can be overridden. Costs are fractions (0.001 == 0.1%).

Nothing about costs places an order: these are estimates applied to simulation
data so reported "st. make" / win-rate numbers reflect a realistic worst case.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Default per-side fee as a fraction (Binance taker ~0.10%).
DEFAULT_FEE_PER_SIDE = 0.001
# Default per-side slippage as a fraction (~0.05% crossing the spread).
DEFAULT_SLIPPAGE_PER_SIDE = 0.0005


@dataclass(frozen=True)
class CostModel:
    fee_per_side: float = DEFAULT_FEE_PER_SIDE
    slippage_per_side: float = DEFAULT_SLIPPAGE_PER_SIDE

    @property
    def per_side(self) -> float:
        return self.fee_per_side + self.slippage_per_side

    @property
    def round_trip(self) -> float:
        """Total cost fraction for a full enter+exit cycle."""
        return 2 * self.per_side

    def net_return(self, gross: Optional[float]) -> Optional[float]:
        """Subtract the round-trip cost from a gross return fraction."""
        if gross is None:
            return None
        return gross - self.round_trip

    def to_dict(self) -> dict:
        return {
            "fee_per_side": self.fee_per_side,
            "slippage_per_side": self.slippage_per_side,
            "per_side": self.per_side,
            "round_trip": self.round_trip,
        }