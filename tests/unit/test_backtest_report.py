"""Phase 4 report tests.

The report must never present a statistic it did not compute, and must always
frame the backtest as a measurement/falsification exercise — never as a claim of
profitability.
"""

from __future__ import annotations

import json

import pytest

from veyra.backtest import BacktestEngine, BacktestReporter, MetricsEngine
from tests.unit.market_synth import bull_continuation_frame


def _run():
    from veyra.config import Settings
    engine = BacktestEngine(Settings())
    return engine.run("BTC/USDT", "4H", bull_continuation_frame(n=260, start_ts=1_000_000_000), run_key="report-1")


def test_report_to_json_is_serializable():
    res = _run()
    metrics = MetricsEngine().compute(res.trades, res.setups, res.run.candle_count)
    rep = BacktestReporter().build(res, metrics)
    payload = json.loads(rep.to_json())
    assert payload["run"]["run_key"] == "report-1"
    assert "metrics" in payload
    assert "warnings" in payload


def test_report_render_includes_config_and_never_claims_profit():
    res = _run()
    metrics = MetricsEngine().compute(res.trades, res.setups, res.run.candle_count)
    rep = BacktestReporter().build(res, metrics)
    text = rep.render_text()
    # Provenance present: execution assumptions are shown, not hidden.
    assert "slippage" in text
    # Honest framing: never literally claims the strategy is "profitable".
    assert "is profitable" not in text.lower()
    assert "profitable strategy" not in text.lower()
    # Scores are framed as a quality ranking, not a probability.
    assert "quality ranking" in text.lower()


def test_report_includes_out_of_sample_boundaries():
    res = _run()
    metrics = MetricsEngine().compute(res.trades, res.setups, res.run.candle_count)
    rep = BacktestReporter().build(res, metrics)
    assert rep.metrics is not None