"""Weighted score aggregation.

Aggregates per-component 0-100 alignment scores into the one canonical
0-100 overall score as a weighted average: overall = Σ wᵢ·cᵢ / Σ wᵢ.

The scale is 0-100 by construction, so every layer (live snapshot, setups,
backtest records, alerts, paper trading) reads the same number and the same
clarity ladder. Note: the overall score is a weighted average of *alignment*
scores (how strongly the components agree), never an estimated probability.
Weights are hypotheses to be validated historically, never treated as
optimal (see config).
"""

from __future__ import annotations

from typing import Dict


class WeightedScoreAggregator:
    def __init__(self, weights: Dict[str, float]):
        total = sum(weights.values())
        if total <= 0:
            raise ValueError("Score weights must sum to a positive value")
        self._weights = weights
        self._total = total

    def aggregate(self, component_scores: Dict[str, int]) -> int:
        """Weighted average of the 0-100 component scores, canonically 0-100."""
        score = 0.0
        for component, weight in self._weights.items():
            value = component_scores.get(component, 0)
            score += value * weight
        return int(round(score / self._total))

    @property
    def weights(self) -> Dict[str, float]:
        return dict(self._weights)

    @property
    def weight_sum(self) -> float:
        """Sum of all configured weights (0.84 by default)."""
        return self._total