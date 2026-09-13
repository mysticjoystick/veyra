"""Tests for the five Phase 3 setup detectors.

Each detector is a pure rule set over a MarketSnapshot, so tests build
snapshots with controlled component meta and assert which candidates fire.
"""

from __future__ import annotations

import pytest

from veyra.domain import (
    DataQualityState,
    MarketSide,
    Regime,
    SetupType,
)
from veyra.strategy.detectors.breakout import BreakoutDetector
from veyra.strategy.detectors.breakout_retest import BreakoutRetestDetector
from veyra.strategy.detectors.pullback import PullbackDetector
from veyra.strategy.detectors.range_rejection import RangeRejectionDetector
from veyra.strategy.detectors.trend_continuation import TrendContinuationDetector
from veyra.strategy.snapshot_view import SnapshotView

from .setup_synth import (
    bear_meta,
    bull_meta,
    hh_hl_meta,
    lh_ll_meta,
    snapshot,
)


# --- Trend continuation ---------------------------------------------------

def test_continuation_long_fires_on_bull():
    view = SnapshotView(
        snapshot(
            regime=Regime.BULL,
            trend_meta=bull_meta(),
            structure_meta=hh_hl_meta(),
            momentum_meta={"momentum": "POSITIVE"},
        )
    )
    cand = TrendContinuationDetector().detect(view)
    assert cand is not None
    assert cand.setup_type == SetupType.TREND_CONTINUATION
    assert cand.side == MarketSide.LONG


def test_continuation_requires_strength():
    view = SnapshotView(
        snapshot(
            regime=Regime.BULL,
            trend_meta=bull_meta(strength=20.0),
            structure_meta=hh_hl_meta(),
            momentum_meta={"momentum": "POSITIVE"},
        )
    )
    assert TrendContinuationDetector().detect(view) is None


def test_continuation_rejects_wrong_structure():
    view = SnapshotView(
        snapshot(
            regime=Regime.BULL,
            trend_meta=bull_meta(),
            structure_meta=lh_ll_meta(),
            momentum_meta={"momentum": "POSITIVE"},
        )
    )
    assert TrendContinuationDetector().detect(view) is None


def test_continuation_short_fires_on_bear():
    view = SnapshotView(
        snapshot(
            regime=Regime.BEAR,
            trend_meta=bear_meta(),
            structure_meta=lh_ll_meta(),
            momentum_meta={"momentum": "NEGATIVE"},
        )
    )
    cand = TrendContinuationDetector().detect(view)
    assert cand is not None
    assert cand.side == MarketSide.SHORT


def test_continuation_rejects_range_regime():
    view = SnapshotView(
        snapshot(
            regime=Regime.RANGE,
            trend_meta=bull_meta(),
            structure_meta=hh_hl_meta(),
            momentum_meta={"momentum": "POSITIVE"},
        )
    )
    assert TrendContinuationDetector().detect(view) is None


def test_continuation_sets_invalidation_and_targets():
    view = SnapshotView(
        snapshot(
            regime=Regime.BULL,
            trend_meta=bull_meta(),
            structure_meta=hh_hl_meta(),
            momentum_meta={"momentum": "POSITIVE"},
        )
    )
    cand = TrendContinuationDetector().detect(view)
    assert cand.invalidation
    assert cand.targets


# --- Pullback -------------------------------------------------------------

def test_pullback_fires_when_retraced():
    # Price pulled back to 105 from strong swing high 108; swing low 100.
    # Retracement = (108-105)/(108-100) = 0.375 -> still within 35%? No.
    # Use a deeper leg so retracement lands inside [2%, 35%].
    view = SnapshotView(
        snapshot(
            regime=Regime.BULL,
            trend_meta=bull_meta(strength=80.0),
            structure_meta=hh_hl_meta(value=106.0, last_swing_high=108.0, last_swing_low=98.0),
            momentum_meta={"momentum": "NEUTRAL", "rsi": 48.0},
        )
    )
    cand = PullbackDetector().detect(view)
    assert cand is not None
    assert cand.setup_type == SetupType.PULLBACK


def test_pullback_rejects_reversal_structure():
    view = SnapshotView(
        snapshot(
            regime=Regime.BULL,
            trend_meta=bull_meta(),
            structure_meta=lh_ll_meta(value=105.0, last_swing_high=108.0, last_swing_low=104.0),
            momentum_meta={"momentum": "NEGATIVE"},
        )
    )
    assert PullbackDetector().detect(view) is None


def test_pullback_rejects_against_trend_momentum():
    view = SnapshotView(
        snapshot(
            regime=Regime.BULL,
            trend_meta=bull_meta(),
            structure_meta=hh_hl_meta(value=105.0, last_swing_high=108.0, last_swing_low=104.0),
            momentum_meta={"momentum": "NEGATIVE"},
        )
    )
    assert PullbackDetector().detect(view) is None


def test_pullback_empty_when_not_retraced():
    # Price at/near the high -> breakout-like, not a pullback.
    view = SnapshotView(
        snapshot(
            regime=Regime.BULL,
            trend_meta=bull_meta(),
            structure_meta=hh_hl_meta(value=108.0, last_swing_high=108.0, last_swing_low=104.0),
            momentum_meta={"momentum": "POSITIVE"},
        )
    )
    assert PullbackDetector().detect(view) is None


# --- Breakout -------------------------------------------------------------

def test_breakout_fires_on_structure_break_up():
    view = SnapshotView(
        snapshot(
            regime=Regime.BULL,
            trend_meta=bull_meta(),
            structure_meta=hh_hl_meta(
                value=112.0,
                last_swing_high=108.0,
                action="BREAK_OF_STRUCTURE_UP",
            ),
            momentum_meta={"momentum": "POSITIVE"},
        )
    )
    cand = BreakoutDetector().detect(view)
    assert cand is not None
    assert cand.setup_type == SetupType.BREAKOUT


def test_breakout_rejects_below_level():
    view = SnapshotView(
        snapshot(
            regime=Regime.BULL,
            trend_meta=bull_meta(),
            structure_meta=hh_hl_meta(value=107.0, last_swing_high=108.0),
            momentum_meta={"momentum": "POSITIVE"},
        )
    )
    assert BreakoutDetector().detect(view) is None


def test_breakout_rejects_false_break_no_action():
    # Above the level but no BREAK_OF_STRUCTURE_UP signal.
    view = SnapshotView(
        snapshot(
            regime=Regime.BULL,
            trend_meta=bull_meta(),
            structure_meta=hh_hl_meta(value=112.0, last_swing_high=108.0, action="NONE"),
            momentum_meta={"momentum": "POSITIVE"},
        )
    )
    assert BreakoutDetector().detect(view) is None


def test_breakout_captures_confirmation_evidence():
    view = SnapshotView(
        snapshot(
            regime=Regime.BULL,
            trend_meta=bull_meta(),
            structure_meta=hh_hl_meta(
                value=112.0, last_swing_high=108.0, action="BREAK_OF_STRUCTURE_UP"
            ),
            momentum_meta={"momentum": "POSITIVE"},
            volume_meta={"volume_state": "EXPANDING", "relative_volume": 1.8, "price_volume_confirmation": True},
            volatility_meta={"volatility_state": "NORMAL"},
        )
    )
    cand = BreakoutDetector().detect(view)
    assert cand.evidence["volume_state"] == "EXPANDING"
    assert cand.evidence["broken_level"] == 108.0


# --- Breakout retest ------------------------------------------------------

def test_retest_fires_near_broken_level():
    view = SnapshotView(
        snapshot(
            regime=Regime.BULL,
            trend_meta=bull_meta(),
            structure_meta=hh_hl_meta(value=109.0, last_swing_high=108.0),
        )
    )
    cand = BreakoutRetestDetector().detect(view)
    assert cand is not None
    assert cand.setup_type == SetupType.BREAKOUT_RETEST


def test_retest_rejects_far_from_level():
    view = SnapshotView(
        snapshot(
            regime=Regime.BULL,
            trend_meta=bull_meta(),
            structure_meta=hh_hl_meta(value=125.0, last_swing_high=108.0),
        )
    )
    assert BreakoutRetestDetector().detect(view) is None


def test_retest_rejects_below_level():
    view = SnapshotView(
        snapshot(
            regime=Regime.BULL,
            trend_meta=bull_meta(),
            structure_meta=hh_hl_meta(value=106.0, last_swing_high=108.0),
        )
    )
    assert BreakoutRetestDetector().detect(view) is None


def test_retest_sets_expiry_condition():
    view = SnapshotView(
        snapshot(
            regime=Regime.BULL,
            trend_meta=bull_meta(),
            structure_meta=hh_hl_meta(value=109.0, last_swing_high=108.0),
        )
    )
    cand = BreakoutRetestDetector().detect(view)
    assert cand.expiry_condition


# --- Range rejection ------------------------------------------------------

def test_range_rejection_short_at_resistance():
    view = SnapshotView(
        snapshot(
            regime=Regime.RANGE,
            trend_meta={"direction": "NEUTRAL", "strength": 40.0},
            structure_meta={
                "structure": "LH_HL",
                "action": "NONE",
                "value": 105.0,
                "last_swing_high": 106.0,
                "last_swing_low": 95.0,
            },
            momentum_meta={"momentum": "NEUTRAL"},
            volatility_meta={"volatility_state": "NORMAL"},
        )
    )
    cand = RangeRejectionDetector().detect(view)
    assert cand is not None
    assert cand.setup_type == SetupType.RANGE_REJECTION
    assert cand.side == MarketSide.SHORT


def test_range_rejection_long_at_support():
    view = SnapshotView(
        snapshot(
            regime=Regime.RANGE,
            trend_meta={"direction": "NEUTRAL", "strength": 40.0},
            structure_meta={
                "structure": "LH_HL",
                "action": "NONE",
                "value": 96.0,
                "last_swing_high": 106.0,
                "last_swing_low": 95.0,
            },
            momentum_meta={"momentum": "NEUTRAL"},
            volatility_meta={"volatility_state": "NORMAL"},
        )
    )
    cand = RangeRejectionDetector().detect(view)
    assert cand is not None
    assert cand.side == MarketSide.LONG


def test_range_rejection_middle_of_range_empty():
    view = SnapshotView(
        snapshot(
            regime=Regime.RANGE,
            trend_meta={"direction": "NEUTRAL", "strength": 40.0},
            structure_meta={
                "structure": "LH_HL",
                "action": "NONE",
                "value": 100.0,
                "last_swing_high": 106.0,
                "last_swing_low": 95.0,
            },
            momentum_meta={"momentum": "NEUTRAL"},
            volatility_meta={"volatility_state": "NORMAL"},
        )
    )
    assert RangeRejectionDetector().detect(view) is None


def test_range_rejection_ignores_directional_regime():
    view = SnapshotView(
        snapshot(
            regime=Regime.BULL,
            trend_meta=bull_meta(),
            structure_meta=hh_hl_meta(value=105.0, last_swing_high=106.0),
        )
    )
    assert RangeRejectionDetector().detect(view) is None