"""Tests for the VolumeEngine."""

from __future__ import annotations

import numpy as np
import pandas as pd

from veyra.domain import IndicatorState, VolumeState
from veyra.market.volume import VolumeEngine

from .market_synth import _frame_from_columns, buy_volume_frame, insufficient_frame


def test_relative_volume_above_one_on_expansion():
    eng = VolumeEngine(volume_ma_period=20, expansion_ratio=1.5, contraction_ratio=0.7)
    res = eng.analyze(buy_volume_frame(n=40, start_ts=1_000_000_000))
    assert res.meta["state"] == IndicatorState.READY.value
    assert res.meta["volume_state"] == VolumeState.EXPANDING.value
    assert res.meta["relative_volume"] > 1.0


def test_price_volume_confirmation_on_up_move():
    eng = VolumeEngine(volume_ma_period=20)
    res = eng.analyze(buy_volume_frame(n=40, start_ts=1_000_000_000))
    assert res.meta["price_volume_confirmation"] is True


def test_contraction_detected():
    closes = np.linspace(100.0, 100.0, 40)
    volume = np.full(40, 1000.0)
    volume[-5:] = 300.0  # contraction at the end
    df = _frame_from_columns(40, 1_000_000_000, closes, volume=volume)
    eng = VolumeEngine(volume_ma_period=20, contraction_ratio=0.7, expansion_ratio=1.5)
    res = eng.analyze(df)
    assert res.meta["volume_state"] == VolumeState.CONTRACTING.value


def test_normal_volume_state():
    closes = np.linspace(100.0, 110.0, 40)
    volume = np.full(40, 1000.0)
    df = _frame_from_columns(40, 1_000_000_000, closes, volume=volume)
    eng = VolumeEngine(volume_ma_period=20)
    res = eng.analyze(df)
    assert res.meta["volume_state"] == VolumeState.NORMAL.value
    assert abs(res.meta["relative_volume"] - 1.0) < 0.001


def test_insufficient_data():
    eng = VolumeEngine(volume_ma_period=20)
    res = eng.analyze(insufficient_frame(n=10, start_ts=1_000_000_000))
    assert res.meta["state"] == IndicatorState.INSUFFICIENT_DATA.value
    assert res.meta["volume_state"] == VolumeState.UNKNOWN.value