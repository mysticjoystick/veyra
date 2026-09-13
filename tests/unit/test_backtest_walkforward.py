"""Phase 4 walk-forward runner tests (out-of-sample windows)."""

from __future__ import annotations

import pytest

from veyra.backtest import WalkForwardRunner
from tests.unit.market_synth import bull_continuation_frame


def _settings():
    from veyra.config import Settings
    return Settings()


def test_walk_forward_produces_independent_test_windows():
    running = WalkForwardRunner(_settings())
    df = bull_continuation_frame(n=600, start_ts=1_000_000_000)
    res = running.run("BTC/USDT", "4H", df, 200, 100, 100, 100)
    assert len(res.windows) == 2
    # Test windows never overlap.
    starts = [m["test"][0] for m in res.window_meta]
    assert starts == sorted(starts)
    # Each window's test portion is measured by distinct candles.
    tests = [set(range(m["test"][0], m["test"][1])) for m in res.window_meta]
    assert tests[0].isdisjoint(tests[1])


def test_walk_forward_deterministic_across_two_runs():
    running = WalkForwardRunner(_settings())
    df = bull_continuation_frame(n=600, start_ts=1_000_000_000)
    a = running.run("BTC/USDT", "4H", df, 200, 100, 100, 100)
    b = running.run("BTC/USDT", "4H", df, 200, 100, 100, 100)
    assert len(a.windows) == len(b.windows)
    for ma, mb in zip(a.windows, b.windows):
        assert ma.to_dict() == mb.to_dict()


def test_walk_forward_returns_metrics_per_window():
    running = WalkForwardRunner(_settings())
    df = bull_continuation_frame(n=600, start_ts=1_000_000_000)
    res = running.run("BTC/USDT", "4H", df, 200, 100, 100, 100)
    assert all("trades" in m for m in res.window_meta)
    # to_dict is JSON-friendly.
    d = res.to_dict()
    assert d["window_count"] == len(res.windows)
    assert "window_meta" in d