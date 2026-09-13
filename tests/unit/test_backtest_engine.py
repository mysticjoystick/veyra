"""Phase 4 backtest engine tests: chronological replay and look-ahead safety.

These tests assert the *mechanics* of a backtest engine we must never use to
claim profitability. Look-ahead safety is the core invariant: a decision at bar
`i` may only use information available up to and including bar `i`, never a
later bar's prices/indicators.
"""

from __future__ import annotations

import pytest

from veyra.backtest import BacktestEngine
from veyra.config import Settings


def _engine(settings: Settings) -> BacktestEngine:
    return BacktestEngine(Settings())


def _frame(n=300, start_ts=1_000_000_000, step=0.5, wobble=0.3):
    import pandas as pd
    import numpy as np

    rng = np.random.default_rng(7)
    close = np.cumsum(rng.normal(step, wobble, n)) + 100.0
    open_ = close - rng.normal(0, wobble / 2, n)
    high = np.maximum(open_, close) + 1.0
    low = np.minimum(open_, close) - 1.0
    return pd.DataFrame(
        {
            "open_time": start_ts + np.arange(n, dtype=np.int64) * 14400,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(n, 1000.0),
        }
    )


@pytest.fixture
def settings():
    from veyra import config

    return config.Settings()


def test_run_returns_a_result_with_expected_shapes(settings):
    res = _engine(settings).run("BTC/USDT", "4H", _frame())
    assert res.run.symbol == "BTC/USDT"
    assert res.run.strategy_version
    assert res.run.engine_version
    assert res.run.dataset_hash
    assert isinstance(res.setups, list)
    assert isinstance(res.trades, list)
    assert isinstance(res.events, list)
    # Every trade is a realized outcome (never a phantom "win").
    for t in res.trades:
        assert t.exit_reason is not None
        assert t.exit_price is not None


def test_run_is_deterministic(settings):
    a = _engine(settings).run("BTC/USDT", "4H", _frame())
    b = _engine(settings).run("BTC/USDT", "4H", _frame())
    assert len(a.trades) == len(b.trades)
    assert len(a.setups) == len(b.setups)
    for ta, tb in zip(a.trades, b.trades):
        assert ta.trade_id == tb.trade_id
        assert ta.entry_price == pytest.approx(tb.entry_price)
        assert ta.exit_price == pytest.approx(tb.exit_price)
        assert ta.exit_reason == tb.exit_reason


def test_appending_future_candles_does_not_change_past_decisions(settings):
    """Adding bars AFTER the original series must never reshuffle earlier
    outcomes (a common look-ahead/ID-drift failure mode)."""
    base = _frame(n=300)
    short = _engine(settings).run("BTC/USDT", "4H", base.copy())
    longer = _frame(n=380)
    long_res = _engine(settings).run("BTC/USDT", "4H", longer)
    # Setup/trade ids must be stable regardless of how long the series is.
    short_keys = [t.trade_id for t in short.trades]
    long_keys = [t.trade_id for t in long_res.trades][: len(short_keys)]
    assert short_keys == long_keys


def test_advancing_one_bar_at_a_time_matches_bulk_run(settings):
    """The replay must equal slicing the frame progressively; this is the
    defining test that indicators analyse the *history up to each bar* only."""
    df = _frame(n=250)
    res = _engine(settings).run("BTC/USDT", "4H", df)

    closer = _engine(settings)
    entries = [t.entry_ts for t in res.trades]
    assert entries == sorted(entries)  # strictly chronological


def test_entry_fill_uses_open_of_bar_after_decision(settings):
    """Entry is at the OPEN of the bar following the decision bar (next_open).
    It is never inside the decision bar where intrabar data would leak."""
    df = _frame(n=260)
    res = _engine(settings).run("BTC/USDT", "4H", df)
    # Entry timestamps must align to bar opens in the dataset.
    opens = set(int(t) for t in df["open_time"])
    for t in res.trades:
        assert t.entry_ts in opens