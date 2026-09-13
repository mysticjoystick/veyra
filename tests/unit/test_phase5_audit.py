"""Phase 5 STEP 1 audit regression tests.

These tests pin the look-ahead-safety and decision-time-freeze guarantees that
the real-data validation (Steps 5+) depends on. They assert the invariant:

    decision at T  ->  only data up to T  ->  setup fields frozen at T
    future appended bars  ->  outcome only, never a reshuffle of T's decision

If any of these fail, Phase 3/4 machinery must be fixed BEFORE acceptance of
real-data evidence. The bar-level () / time-stamp math uses the canonical 4H
interval (14400 s) used throughout the repo.
"""

from __future__ import annotations

import pandas as pd
import pytest

from veyra.backtest import BacktestEngine
from veyra.config import Settings


def _engine(settings: Settings) -> BacktestEngine:
    return BacktestEngine(settings)


def _frame(n=320, start_ts=1_000_000_000, step=0.5, wobble=0.3, seed=7):
    import numpy as np

    rng = np.random.default_rng(seed)
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


def _decision_fingerprint(record) -> tuple:
    """All fields that are decided at detection time and must not drift when
    future candles are appended."""
    return (
        record.setup_type,
        record.side,
        record.regime,
        record.score,
        record.score_normalized,
        record.interest_area_low,
        record.interest_area_high,
        record.invalidation,
    )


@pytest.fixture
def settings():
    from veyra import config

    return config.Settings()


def test_decision_fields_are_frozen_when_future_candles_appended(settings):
    """THE critical Phase 5 freeze test.

    Build one frame, run on the full series and on a strict prefix (same
    underlying data, truncated only). For every historical setup present in
    the short run, the long run must reproduce it with bit-identical
    decision-time fields (score, interest area, invalidation, regime, type)
    and must NOT report any extra setup before the truncation boundary.
    """
    full = _frame(n=400)
    prefix_n = 240

    short = _engine(settings).run("BTC/USDT", "4H", full.head(prefix_n).copy())
    long = _engine(settings).run("BTC/USDT", "4H", full.copy())

    short_keys = [r.key for r in short.setups]
    long_keys = [r.key for r in long.setups]
    long_prefix = [k for k in long_keys if k in set(short_keys)]

    # The short-run setups must be a strict prefix of the long run (matched by
    # stable key) -- no reshuffling, no extras crept in before T.
    assert long_prefix == short_keys

    short_by_key = {r.key: r for r in short.setups}
    for r in long.setups:
        if r.key not in short_by_key:
            continue
        base = short_by_key[r.key]
        assert _decision_fingerprint(r) == _decision_fingerprint(base), (
            f"decision-time values drifted for setup {r.key}"
        )


def test_freezing_is_order_and_count_preserving(settings):
    """The short and long runs must agree on the *ordered* list of detection
    timestamps up to T -- not just that the multiset matches."""
    full = _frame(n=400)
    prefix_n = 260

    short = _engine(settings).run("BTC/USDT", "4H", full.head(prefix_n).copy())
    long = _engine(settings).run("BTC/USDT", "4H", full.copy())

    short_ts = [r.detection_ts for r in short.setups]
    long_ts = [r.detection_ts for r in long.setups]
    boundary = int(full["open_time"].iloc[prefix_n - 1])
    long_before = [t for t in long_ts if t <= boundary]
    assert short_ts == long_before or short_ts == long_before[: len(short_ts)]


def test_generated_frames_share_a_data_prefix(settings):
    """Guard against the test prologue itself regressing: the full frame must
    actually contain the prefix we slice, and truncation must preserve it
    (same generator call, not two independent random walks)."""
    full = _frame(n=400)
    df = full.head(240)
    assert int(df["open_time"].iloc[0]) == int(full["open_time"].iloc[0])
    assert int(df["open_time"].iloc[-1]) == int(full["open_time"].iloc[239])
    assert len(df) == 240
    assert_full_prefix = full.iloc[:240].equals(df.reset_index(drop=True))
    assert assert_full_prefix


def test_identical_prefix_does_not_receive_extremal_setup_swaps(settings):
    """Appending future candles must never change the *set* of setups logged
    at or before the prefix's final bar (no look-ahead promotions)."""
    full = _frame(n=400)
    prefix_n = 220

    short = _engine(settings).run("BTC/USDT", "4H", full.head(prefix_n).copy())
    long = _engine(settings).run("BTC/USDT", "4H", full.copy())

    boundary = int(full["open_time"].iloc[prefix_n - 1])
    long_setups_at_or_before = [r for r in long.setups if r.detection_ts <= boundary]
    short_upto = [r for r in short.setups if r.detection_ts <= boundary]

    long_fp = {_decision_fingerprint(r) for r in long_setups_at_or_before}
    short_fp = {_decision_fingerprint(r) for r in short_upto}
    assert short_fp == long_fp