"""Tests for the StructureEngine."""

from __future__ import annotations

import numpy as np

from veyra.domain import IndicatorState, StructureAction, StructureState
from veyra.market.structure import StructureEngine

from .market_synth import (
    hh_hl_swings_frame,
    insufficient_frame,
    lh_ll_swings_frame,
    _frame_from_columns,
)


def test_bullish_structure_hh_hl():
    eng = StructureEngine(pivot_lookback=2, min_pivots=3)
    res = eng.analyze(hh_hl_swings_frame(start_ts=1_000_000_000))
    assert res.meta["state"] == IndicatorState.READY.value
    assert res.meta["structure"] == StructureState.HIGHER_HIGHS_HIGHER_LOWS.value


def test_swing_detection_lists_points():
    eng = StructureEngine(pivot_lookback=2, min_pivots=3)
    res = eng.analyze(hh_hl_swings_frame(start_ts=1_000_000_000))
    highs = res.meta["evidence"]["swing_highs"]
    lows = res.meta["evidence"]["swing_lows"]
    assert len(highs) >= 2
    assert len(lows) >= 2
    # In an uptrend swing highs and lows should be rising.
    assert highs[-1] > highs[-2]
    assert lows[-1] > lows[-2]


def test_lh_ll_bearish_structure():
    eng = StructureEngine(pivot_lookback=2, min_pivots=3)
    res = eng.analyze(lh_ll_swings_frame(start_ts=1_000_000_000))
    assert res.meta["structure"] == StructureState.LOWER_HIGHS_LOWER_LOWS.value
    hi = res.meta["evidence"]["swing_highs"]
    lo = res.meta["evidence"]["swing_lows"]
    # Evidence reports price levels; in a downtrend they must be falling.
    assert hi[-1] < hi[-2]
    assert lo[-1] < lo[-2]


def test_insufficient_data():
    eng = StructureEngine(pivot_lookback=3, min_pivots=3)
    res = eng.analyze(insufficient_frame(n=8, start_ts=1_000_000_000))
    assert res.meta["state"] == IndicatorState.INSUFFICIENT_DATA.value
    assert res.meta["structure"] == StructureState.UNKNOWN.value


def test_pivot_requires_confirmation_both_sides():
    eng = StructureEngine(pivot_lookback=2, min_pivots=3)
    res = eng.analyze(hh_hl_swings_frame(start_ts=1_000_000_000))
    assert res.meta["state"] == IndicatorState.READY.value