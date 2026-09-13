"""Phase 5 STEP 4 tests: frozen baseline config.

Pins that the frozen ''phase5-baseline-v1'' strategy stamps its version on
every run and that its recorded settings match the production defaults exactly
(no drift between the recorded baseline and what actually executes).
"""

from __future__ import annotations

import pandas as pd
import pytest

from veyra.backtest import BacktestEngine
from veyra.config import Settings


def _frame(n=260, start_ts=1_000_000_000):
    import numpy as np

    rng = np.random.default_rng(4)
    close = np.cumsum(rng.normal(0.5, 0.3, n)) + 100.0
    return pd.DataFrame(
        {
            "open_time": start_ts + np.arange(n, dtype=np.int64) * 14400,
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": np.full(n, 1000.0),
        }
    )


@pytest.fixture
def settings():
    from veyra import config

    return config.Settings()


def test_baseline_engine_stamps_frozen_version(settings):
    eng = BacktestEngine(settings, strategy_version="phase5-baseline-v1")
    res = eng.run("BTC/USDT", "4H", _frame(), run_key="frozen-stamp")
    assert res.run.strategy_version == "phase5-baseline-v1"


def test_default_engine_still_stamps_legacy_version(settings):
    # Phase 3/4 identity must be preserved unless an explicit version is passed.
    res = BacktestEngine(settings).run("BTC/USDT", "4H", _frame(), run_key="legacy")
    assert res.run.strategy_version == "veyra-3.3"


def test_frozen_record_matches_settings_defaults(settings):
    from veyra.phase5 import snapshot_settings

    s = snapshot_settings(settings)
    assert s["strategy_version"] == "phase5-baseline-v1"
    assert s["execution"]["entry_fee_pct"] == settings.backtest_entry_fee_pct
    assert s["execution"]["exit_fee_pct"] == settings.backtest_exit_fee_pct
    assert s["execution"]["entry_policy"] == settings.backtest_entry_policy
    assert s["execution"]["overlap_policy"] == settings.backtest_overlap_policy
    assert s["scoring_weights"]["trend"] == settings.weight_trend
    assert s["setup_thresholds"]["min_qualify_score"] == settings.setup_min_qualify_score