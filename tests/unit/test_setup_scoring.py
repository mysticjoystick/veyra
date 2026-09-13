"""Tests for setup scoring: component caps, weights, bounds, missing data."""

from __future__ import annotations

from veyra.domain import (
    AnalyticsComponent,
    MarketSide,
    Regime,
    Setup,
    SetupType,
)
from veyra.strategy.aggregation import WeightedScoreAggregator
from veyra.strategy.scoring import SetupScorer
from veyra.strategy.snapshot_view import SnapshotView

from .setup_synth import bear_meta, bull_meta, hh_hl_meta, lh_ll_meta, snapshot


def _engine(weights):
    agg = WeightedScoreAggregator(weights)
    return SetupScorer(agg), agg


def test_overall_score_is_weighted_average():
    scorer, _ = _engine(
        {"TREND": 0.25, "STRUCTURE": 0.20, "PULLBACK": 0.15, "MOMENTUM": 0.12,
         "VOLUME": 0.07, "VOLATILITY": 0.05}
    )
    view = SnapshotView(
        snapshot(
            regime=Regime.BULL,
            trend_meta=bull_meta(),
            structure_meta=hh_hl_meta(),
            momentum_meta={"momentum": "POSITIVE"},
        )
    )
    setup = Setup(
        symbol="BTC/USDT", timeframe="4H", timestamp=view.timestamp,
        setup_type=SetupType.TREND_CONTINUATION, side=MarketSide.LONG, regime=Regime.BULL,
    )
    scorer.apply(setup, view)
    # Overall = weighted average of component scores, canonical 0-100.
    expected = round(
        (setup.scores[AnalyticsComponent.TREND] * 0.25
         + setup.scores[AnalyticsComponent.STRUCTURE] * 0.20
         + setup.scores[AnalyticsComponent.PULLBACK] * 0.15
         + setup.scores[AnalyticsComponent.MOMENTUM] * 0.12
         + setup.scores[AnalyticsComponent.VOLUME] * 0.07
         + setup.scores[AnalyticsComponent.VOLATILITY] * 0.05)
        / 0.84
    )
    assert setup.overall_score == expected


def test_score_bounds_and_components_present():
    scorer, _ = _engine(
        {"TREND": 0.25, "STRUCTURE": 0.20, "PULLBACK": 0.15, "MOMENTUM": 0.12,
         "VOLUME": 0.07, "VOLATILITY": 0.05}
    )
    view = SnapshotView(
        snapshot(
            regime=Regime.BULL,
            trend_meta=bull_meta(),
            structure_meta=hh_hl_meta(),
            momentum_meta={"momentum": "POSITIVE"},
        )
    )
    setup = Setup(
        symbol="BTC/USDT", timeframe="4H", timestamp=1_000_000_000,
        setup_type=SetupType.TREND_CONTINUATION, side=MarketSide.LONG, regime=Regime.BULL,
    )
    scorer.apply(setup, view)
    names = [c.value for c in setup.scores.keys()]
    assert "TREND" in names and "STRUCTURE" in names and "PULLBACK" in names
    assert "MOMENTUM" in names and "VOLUME" in names and "VOLATILITY" in names
# Each component is 0-100.
    for v in setup.scores.values():
        assert 0 <= v <= 100
    # Overall is a canonical 0-100 weighted average.
    assert setup.overall_score <= 100
    assert setup.overall_score >= 0


def test_missing_components_contribute_zero():
    agg = WeightedScoreAggregator({"TREND": 0.5, "MOMENTUM": 0.5})
    assert agg.aggregate({"TREND": 100}) == 50
    assert agg.aggregate({}) == 0


def test_genuine_component_scores_are_consumed():
    """Regression: the scorer must read each engine's real alignment score from
    AnalysisComponentOutput.score, NOT a bogus 'score' key in the meta dict
    (the historic dead-score bug that zeroed TREND/STRUCTURE/MOMENTUM/VOLUME)."""
    scorer, _ = _engine(
        {"TREND": 0.25, "STRUCTURE": 0.20, "PULLBACK": 0.15, "MOMENTUM": 0.12,
         "VOLUME": 0.07, "VOLATILITY": 0.05}
    )
    view = SnapshotView(
        snapshot(
            regime=Regime.BULL,
            trend_meta=dict(bull_meta(), strength=80.0),    # meta has NO 'score' key
            structure_meta=dict(hh_hl_meta(), structure="HH_HL"),
            momentum_meta={"momentum": "POSITIVE"},
            volume_meta={"price_volume_confirmation": False},
        )
    )
    # setup_synth places the genuine engine scores in components: TREND=90,
    # STRUCTURE=85, MOMENTUM=70, VOLUME=60. These must NOT come out as 0.
    setup = Setup(
        symbol="BTC/USDT", timeframe="4H", timestamp=view.timestamp,
        setup_type=SetupType.TREND_CONTINUATION, side=MarketSide.LONG, regime=Regime.BULL,
    )
    scorer.apply(setup, view)
    assert setup.scores[AnalyticsComponent.TREND] == 90
    assert setup.scores[AnalyticsComponent.STRUCTURE] == 85
    assert setup.scores[AnalyticsComponent.MOMENTUM] == 70
    assert setup.scores[AnalyticsComponent.VOLUME] == 60
    # overall must reflect the genuine (formerly-zeroed) components
    assert setup.overall_score > 0


def test_opposing_structure_penalised():
    scorer, _ = _engine(
        {"TREND": 1.0, "STRUCTURE": 1.0, "PULLBACK": 1.0, "MOMENTUM": 1.0,
         "VOLUME": 1.0, "VOLATILITY": 1.0}
    )
    view = SnapshotView(
        snapshot(
            regime=Regime.BEAR,
            trend_meta=bear_meta(),
            structure_meta=hh_hl_meta(value=90.0, last_swing_high=None),  # opposed
            momentum_meta={"momentum": "NEGATIVE"},
        )
    )
    setup = Setup(
        symbol="BTC/USDT", timeframe="4H", timestamp=1_000_000_000,
        setup_type=SetupType.TREND_CONTINUATION, side=MarketSide.SHORT, regime=Regime.BEAR,
    )
    scorer.apply(setup, view)
    # Structure opposes the SHORT -> heavily penalised below its raw score.
    assert setup.scores[AnalyticsComponent.STRUCTURE] < 50
