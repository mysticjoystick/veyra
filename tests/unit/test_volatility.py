"""Tests for the VolatilityEngine."""

from __future__ import annotations

import numpy as np

from veyra.domain import IndicatorState, VolatilityState
from veyra.market.volatility import VolatilityEngine

from .market_synth import _frame_from_columns, expansion_frame, insufficient_frame


def test_high_volatility_detected():
    eng = VolatilityEngine(
        atr_period=14,
        low_pct=0.6,
        high_pct=1.4,
        extreme_pct=2.0,
    )
    res = eng.analyze(expansion_frame(n=120, start_ts=1_000_000_000))
    assert res.meta["state"] == IndicatorState.READY.value
    assert res.meta["volatility_state"] in (
        VolatilityState.HIGH.value,
        VolatilityState.EXTREME.value,
    )
    assert res.meta["atr_percent"] > 0


def test_controlled_volatility_normal():
    closes = np.linspace(100.0, 110.0, 80)
    df = _frame_from_columns(80, 1_000_000_000, closes)
    eng = VolatilityEngine(atr_period=14)
    res = eng.analyze(df)
    assert res.meta["volatility_state"] == VolatilityState.NORMAL.value


def test_expansion_marks_high():
    closes = np.concatenate(
        [np.linspace(100.0, 102.0, 60), np.linspace(102.0, 180.0, 20)]
    )
    df = _frame_from_columns(80, 1_000_000_000, closes)
    eng = VolatilityEngine(atr_period=14, high_pct=1.4, extreme_pct=2.0)
    res = eng.analyze(df)
    assert res.meta["volatility_state"] in (
        VolatilityState.HIGH.value,
        VolatilityState.EXTREME.value,
    )
    assert res.meta["expansion_ratio"] > 1.0


def test_insufficient_data():
    eng = VolatilityEngine(atr_period=14)
    res = eng.analyze(insufficient_frame(n=10, start_ts=1_000_000_000))
    assert res.meta["state"] == IndicatorState.INSUFFICIENT_DATA.value
    assert res.meta["volatility_state"] == VolatilityState.UNKNOWN.value