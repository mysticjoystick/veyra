"""Integration tests for the market analysis pipeline.

Candles -> Trend -> Structure -> Momentum -> Volume -> Volatility ->
Regime -> MarketSnapshot.
"""

from __future__ import annotations

import pytest

from veyra.domain import Regime, SystemState
from veyra.market.pipeline import MarketAnalysisPipeline

from .market_synth import (
    higher_highs_higher_lows_frame,
    insufficient_frame,
)
from .helpers import candles_to_frame, make_candles


def test_pipeline_builds_full_snapshot_on_bullish_data(settings):
    pipeline = MarketAnalysisPipeline.default(settings)
    data = higher_highs_higher_lows_frame(n=300, start_ts=1_000_000_000)
    snapshot = pipeline.analyze("BTC/USDT", "4H", data)

    assert snapshot.symbol == "BTC/USDT"
    assert snapshot.timeframe == "4H"
    assert snapshot.timestamp == int(data["open_time"].iloc[-1])

    # All five components recorded.
    for name in ("TREND", "STRUCTURE", "MOMENTUM", "VOLUME", "VOLATILITY"):
        assert name in snapshot.components

    # Trend is BULLISH, structure is HH_HL -> regime BULL.
    assert snapshot.components["TREND"].meta["direction"] == "BULLISH"
    assert snapshot.components["STRUCTURE"].meta["structure"] == "HH_HL"
    assert snapshot.regime == Regime.BULL
    assert snapshot.regime_output.score > 0
    assert snapshot.scores  # component scores populated

    # Serializability.
    d = snapshot.to_dict()
    assert d["symbol"] == "BTC/USDT"
    assert d["regime"] == Regime.BULL.value
    assert "components" in d
    assert "data_quality" in d


def test_pipeline_reports_insufficient_data_quality(settings):
    pipeline = MarketAnalysisPipeline.default(settings)
    data = insufficient_frame(n=30, start_ts=1_000_000_000)
    snapshot = pipeline.analyze("ETH/USDT", "4H", data)

    # Engines report insufficient data individually.
    assert snapshot.components["TREND"].state == "INSUFFICIENT_DATA"
    # Data-quality state reflects insufficient lookback.
    assert snapshot.data_quality.is_insufficient
    assert snapshot.regime == Regime.UNKNOWN


def test_pipeline_snapshot_serializable_has_evidence(settings):
    pipeline = MarketAnalysisPipeline.default(settings)
    snapshot = pipeline.analyze(
        "BTC/USDT", "1D", higher_highs_higher_lows_frame(n=260)
    )
    d = snapshot.to_dict()
    trend_meta = d["components"]["TREND"]
    assert "direction" in trend_meta["meta"]
    assert "evidence" in trend_meta
    assert d["data_quality"]["row_count"] == 252


def test_pipeline_raises_on_empty_data(settings):
    pipeline = MarketAnalysisPipeline.default(settings)
    with pytest.raises(ValueError):
        pipeline.analyze("BTC/USDT", "4H", candles_to_frame([]))


def test_data_quality_only_reports_when_given_enough(settings):
    # A small-but-valid frame still resolves to INSUFFICIENT_DATA for the
    # strictest engine (trend needs >210 candles).
    pipeline = MarketAnalysisPipeline.default(settings)
    data = candles_to_frame(make_candles(n=100, start_ts=1_000_000_000))
    snapshot = pipeline.analyze("BTC/USDT", "4H", data)
    assert snapshot.data_quality.is_insufficient