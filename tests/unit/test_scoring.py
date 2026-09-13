"""Tests for the weighted score aggregator."""

from __future__ import annotations

import pytest

from veyra.strategy.setup_engine import WeightedScoreAggregator


def test_aggregator_weights_sum_scores():
    agg = WeightedScoreAggregator(
        {"TREND": 0.25, "STRUCTURE": 0.20, "PULLBACK": 0.15, "MOMENTUM": 0.12,
         "VOLUME": 0.07, "VOLATILITY": 0.05}
    )
    result = agg.aggregate(
        {"TREND": 91, "STRUCTURE": 87, "PULLBACK": 78, "MOMENTUM": 73,
         "VOLUME": 64, "VOLATILITY": 70}
    )
    # Canonical score = weighted average (Σ wᵢcᵢ / Σ wᵢ) on a 0-100 scale.
    expected = round(
        (91 * 0.25 + 87 * 0.20 + 78 * 0.15 + 73 * 0.12 + 64 * 0.07 + 70 * 0.05)
        / 0.84
    )
    assert result == expected


def test_aggregator_missing_components_contribute_zero():
    agg = WeightedScoreAggregator({"TREND": 0.5, "MOMENTUM": 0.5})
    assert agg.aggregate({"TREND": 100}) == 50


def test_aggregator_rejects_non_positive_total():
    with pytest.raises(ValueError):
        WeightedScoreAggregator({"TREND": 0.0, "MOMENTUM": 0.0})


def test_weights_are_copied_not_shared():
    agg = WeightedScoreAggregator({"TREND": 0.5})
    agg.weights["TREND"] = 0.9
    assert agg._weights["TREND"] == 0.5
