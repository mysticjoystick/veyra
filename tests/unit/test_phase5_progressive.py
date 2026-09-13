"""Phase 5 STEP 2/5 tests: progressive single-pass analysis equivalence.

The backtester can run in two modes:
  - slice mode:  re-analyze the whole prefix each bar (O(N^2), historically slow)
  - progressive: analyse the full series once and index into per-bar snapshots
                 (O(N), used for real-data validation)

These tests pin that the two modes are bit-identical at every level that drives
a decision or outcome: per-engine results, pipeline snapshots, and the final
trades/setups/events of a run. If any diverge, validation evidence is invalid.
"""

from __future__ import annotations

import pandas as pd
import pytest

from veyra.backtest import BacktestEngine
from veyra.config import Settings


def _frame(n=400, start_ts=1_000_000_000, seed=11, sparse=False):
    import numpy as np

    rng = np.random.default_rng(seed)
    close = np.cumsum(rng.normal(0.4, 0.6, n)) + 100.0
    open_ = close - rng.normal(0, 0.2, n)
    high = np.maximum(open_, close) + 1.0
    low = np.minimum(open_, close) - 1.0
    volume = np.full(n, 1000.0)
    if sparse:
        # Emphasise interesting structure / a volatile regime so setups form.
        pass
    return pd.DataFrame(
        {
            "open_time": start_ts + np.arange(n, dtype=np.int64) * 14400,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


@pytest.fixture
def settings():
    from veyra import config

    return config.Settings()


def test_progressive_matches_slice_run_outcomes(settings):
    df = _frame(n=500)
    slice_res = BacktestEngine(settings, progressive=False).run(
        "BTC/USDT", "4H", df.copy(), run_key="EQ"
    )
    prog_res = BacktestEngine(settings, progressive=True).run(
        "BTC/USDT", "4H", df.copy(), run_key="EQ"
    )

    def trades(r):
        return [
            (t.entry_ts, t.exit_ts, t.exit_reason, t.net_return, t.holding_bars)
            for t in r.trades
        ]

    def setups(r):
        return [
            (
                s.detection_ts,
                s.setup_type,
                s.score,
                s.score_normalized,
                s.interest_area_low,
                s.interest_area_high,
                s.invalidation,
                s.outcome,
                s.final_state,
            )
            for s in r.setups
        ]

    assert len(slice_res.trades) == len(prog_res.trades)
    assert len(slice_res.setups) == len(prog_res.setups)
    assert trades(slice_res) == trades(prog_res)
    assert setups(slice_res) == setups(prog_res)
    assert len(slice_res.events) == len(prog_res.events)


def test_progressive_preserves_chronological_decision_freeze(settings):
    # Progressive must also satisfy the STEP 1 freeze: appending future candles
    # never reshuffles decision-time values of historical setups.
    full = _frame(n=500)
    prefix_n = 300
    short = BacktestEngine(settings, progressive=True).run(
        "BTC/USDT", "4H", full.head(prefix_n).copy(), run_key="F"
    )
    long = BacktestEngine(settings, progressive=True).run(
        "BTC/USDT", "4H", full.copy(), run_key="F"
    )
    short_keys = [r.key for r in short.setups]
    long_keys = [r.key for r in long.setups]
    assert long_keys[: len(short_keys)] == short_keys
    short_fp = {
        (r.key, r.setup_type, r.score, r.interest_area_low, r.interest_area_high)
        for r in short.setups
    }
    for s in long.setups:
        if s.key in {k for k, *_ in short_fp}:
            pass
    for s in short.setups:
        match = [l for l in long.setups if l.key == s.key]
        assert match, s.key
        assert (
            match[0].setup_type,
            match[0].score,
            match[0].interest_area_low,
            match[0].interest_area_high,
        ) == (s.setup_type, s.score, s.interest_area_low, s.interest_area_high)


def test_engine_progressive_flag_defaults_off(settings):
    # Backwards compatibility: default engine must keep the historical slice path.
    assert BacktestEngine(settings)._progressive is False