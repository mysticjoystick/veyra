"""Score normalisation layer (retained for API compatibility).

Scoring now produces a single canonical 0-100 score directly: the
WeightedScoreAggregator returns the weighted average of the component 0-100
alignment scores, so the whole system (live snapshot, setups, backtest
records, alerts, paper trading) shares one scale and one clarity ladder.

This class is kept as an identity/clamping adapter so call sites that read
``score_normalized`` keep working without nested scaling. Consumers should read
``overall_score`` / ``score_normalized`` interchangeably: both are the same
0-100 number.
"""

from __future__ import annotations


class ScoreNormalizer:
    """Pass-through of the canonical 0-100 score, clamped to [0, 100]."""

    def __init__(self, weight_sum: float) -> None:
        if weight_sum <= 0:
            raise ValueError("weight_sum must be positive")
        self._weight_sum = float(weight_sum)

    def normalized(self, raw_score: int) -> int:
        """Canonical 0-100 score (already normalized), clamped to [0, 100]."""
        return int(max(0.0, min(100.0, round(raw_score))))

    @property
    def weight_sum(self) -> float:
        """Aggregator weight sum (kept for parity/back-compat, not a rescale)."""
        return self._weight_sum

    @property
    def raw_max(self) -> int:
        """Maximum canonical score (100)."""
        return 100


def default_weight_sum(
    trend: float = 0.25,
    structure: float = 0.20,
    pullback: float = 0.15,
    momentum: float = 0.12,
    volume: float = 0.07,
    volatility: float = 0.05,
) -> float:
    """Sum of the default Phase 3 weights (0.84)."""
    return trend + structure + pullback + momentum + volume + volatility