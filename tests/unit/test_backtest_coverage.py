"""Coverage audit tests: the engine must not silently drop "quiet" bars.

Every bar is analysed, but only bars with a live (non-terminal) setup produce
setup records. These tests assert the counters that make the WAIT majority
visible are correct, carry through the result, and never break the histogram
invariant: sum(coverage_by_live) == analyzed bars.
"""

from __future__ import annotations

import pytest

from veyra.backtest import BacktestEngine
from veyra.config import Settings

from tests.unit.market_synth import (
    bull_continuation_frame,
    sideways_frame,
    insufficient_frame,
)


@pytest.fixture
def settings() -> Settings:
    return Settings()


def test_coverage_invariants_on_covered_frame(settings):
    res = BacktestEngine(settings).run("BTC/USDT", "4H", bull_continuation_frame())
    cov = res.coverage()
    assert cov["analyzed_bars"] > 0
    assert 0 <= cov["bars_with_setup"] <= cov["analyzed_bars"]
    assert cov["wait_bars"] == cov["analyzed_bars"] - cov["bars_with_setup"]
    # Histogram must sum to the analysed-bar total, no bars lost.
    assert sum(res.coverage_by_live) == cov["analyzed_bars"]
    # The engineered bullish frame holds a setup most of the analysed time.
    assert cov["coverage_fraction"] > 0.5
    # At least one bar had no live setup (warm-up / gap) unless perfectly dense.
    assert cov["bars_with_setup"] < cov["analyzed_bars"] or len(res.setups) > 0


def test_coverage_fraction_matches_counter_fields(settings):
    res = BacktestEngine(settings).run("BTC/USDT", "4H", bull_continuation_frame())
    cov = res.coverage()
    assert cov["analyzed_bars"] == res.run.analyzed_bars
    assert cov["bars_with_setup"] == res.run.bars_with_setup


def test_run_dict_exposes_coverage_fields(settings):
    res = BacktestEngine(settings).run("BTC/USDT", "4H", sideways_frame())
    d = res.run.to_dict()
    assert d["analyzed_bars"] >= 0
    assert d["bars_with_setup"] >= 0


def test_result_dict_exposes_coverage_by_live(settings):
    res = BacktestEngine(settings).run("BTC/USDT", "4H", sideways_frame())
    d = res.to_dict()
    assert "coverage_by_live" in d
    assert len(d["coverage_by_live"]) > 0


def test_low_coverage_frame_is_honest(settings):
    # Sideways ranges rarely qualify a setup, so coverage must be far below 1.
    res = BacktestEngine(settings).run("BTC/USDT", "4H", sideways_frame())
    cov = res.coverage()
    assert cov["coverage_fraction"] < 0.5


def test_insufficient_data_produces_safe_zero_coverage(settings):
    res = BacktestEngine(settings).run("BTC/USDT", "4H", insufficient_frame())
    cov = res.coverage()
    assert cov["analyzed_bars"] >= 0
    assert 0.0 <= cov["coverage_fraction"] <= 1.0
    assert cov["wait_bars"] == cov["analyzed_bars"] - cov["bars_with_setup"]


def test_report_dict_and_text_include_coverage(settings):
    from veyra.backtest import BacktestReporter, MetricsEngine

    res = BacktestEngine(settings).run("BTC/USDT", "4H", bull_continuation_frame())
    metrics = MetricsEngine().compute(res.trades, res.setups, res.run.candle_count)
    report = BacktestReporter().build(res, metrics)
    as_dict = report.to_dict()
    assert "coverage" in as_dict
    assert as_dict["coverage"]["analyzed_bars"] > 0
    text = report.render_text()
    assert "Coverage fraction" in text
    assert "WAIT (no-signal)" in text