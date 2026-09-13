"""Tests for the SetupEngine: detection gating, lifecycle advance, persistence."""

from __future__ import annotations

import pytest

from veyra.domain import (
    DataQualityState,
    Regime,
    SetupState,
)
from veyra.strategy.setup_engine import SetupEngine
from veyra.strategy.snapshot_view import SnapshotView

from .setup_synth import bear_meta, bull_meta, hh_hl_meta, lh_ll_meta, snapshot


def _engine(settings):
    return SetupEngine(settings)


def test_detect_returns_zero_on_insufficient_data(settings):
    eng = _engine(settings)
    snap = snapshot(
        regime=Regime.BULL,
        trend_meta=bull_meta(),
        structure_meta=hh_hl_meta(),
        momentum_meta={"momentum": "POSITIVE"},
        data_quality_state=DataQualityState.INSUFFICIENT_DATA,
    )
    assert eng.detect(snap) == []


def test_detect_returns_zero_on_invalid_data(settings):
    eng = _engine(settings)
    snap = snapshot(
        regime=Regime.BULL,
        trend_meta=bull_meta(),
        structure_meta=hh_hl_meta(),
        momentum_meta={"momentum": "POSITIVE"},
        data_quality_state=DataQualityState.INVALID,
    )
    assert eng.detect(snap) == []


def test_detect_promotes_valid_bull_snapshot(settings):
    eng = _engine(settings)
    snap = snapshot(
        regime=Regime.BULL,
        trend_meta=bull_meta(strength=80.0),
        structure_meta=hh_hl_meta(value=110.0, last_swing_high=108.0, action="BREAK_OF_STRUCTURE_UP"),
        momentum_meta={"momentum": "POSITIVE"},
        volume_meta={"volume_state": "EXPANDING", "relative_volume": 1.8, "price_volume_confirmation": True},
        volatility_meta={"volatility_state": "NORMAL"},
    )
    setups = eng.detect(snap)
    # A BULL HH_HL with a breakout BOS should produce a breakout + continuation
    # candidate (and possibly retest). At minimum a valid, scored setup exists.
    assert setups
    for s in setups:
        assert s.state.value == "DETECTED"
        assert s.overall_score >= 0
        assert s.evidence
        assert s.to_dict()["symbol"] == "BTC/USDT"


def test_detect_conservative_produces_none_no_setup(settings):
    eng = _engine(settings)
    # NEUTRAL trend, RANGE regime, middle of range -> nothing should fire.
    snap = snapshot(
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
    assert eng.detect(snap) == []


def test_advance_developing_to_qualified(settings):
    eng = _engine(settings)
    snap = snapshot(
        regime=Regime.BULL,
        trend_meta=bull_meta(),
        structure_meta=hh_hl_meta(),
        momentum_meta={"momentum": "POSITIVE"},
    )
    setups = eng.detect(snap)
    assert setups
    setup = setups[0]
    # Advance with a fresh snapshot that still confirms.
    later = snapshot(
        regime=Regime.BULL,
        trend_meta=bull_meta(),
        structure_meta=hh_hl_meta(),
        momentum_meta={"momentum": "POSITIVE"},
        timestamp=1_000_000_000 + 14400,
    )
    _ = eng.advance(setup, later)
    assert setup.state.value in ("DEVELOPING", "QUALIFIED")


def test_advance_invalidates_on_opposing_structure(settings):
    eng = _engine(settings)
    snap = snapshot(
        regime=Regime.BULL,
        trend_meta=bull_meta(),
        structure_meta=hh_hl_meta(),
        momentum_meta={"momentum": "POSITIVE"},
    )
    setup = eng.detect(snap)[0]
    # Later: same BULL regime but structure flips against the LONG side.
    later = snapshot(
        regime=Regime.BULL,
        trend_meta=bull_meta(strength=20.0),
        structure_meta=lh_ll_meta(),
        momentum_meta={"momentum": "NEGATIVE"},
        timestamp=1_000_000_000 + 14400,
    )
    new_state = eng.advance(setup, later)
    assert new_state == SetupState.INVALIDATED
    assert setup.state.value == "INVALIDATED"


def test_advance_expires_after_max_lifetime(settings):
    eng = _engine(settings)
    snap = snapshot(
        regime=Regime.BULL,
        trend_meta=bull_meta(),
        structure_meta=hh_hl_meta(),
        momentum_meta={"momentum": "POSITIVE"},
    )
    setup = eng.detect(snap)[0]
    # Far-future snapshot (beyond max lifetime bars) but same regime/momentum.
    long_later = snapshot(
        regime=Regime.BULL,
        trend_meta=bull_meta(),
        structure_meta=hh_hl_meta(),
        momentum_meta={"momentum": "POSITIVE"},
        timestamp=1_000_000_000 + 14400 * (settings.setup_max_lifetime_bars + 5),
    )
    new_state = eng.advance(setup, long_later)
    assert setup.state.value == "EXPIRED"


def test_terminal_state_not_advanced(settings):
    from veyra.domain import SetupState

    eng = _engine(settings)
    snap = snapshot(
        regime=Regime.BULL,
        trend_meta=bull_meta(),
        structure_meta=hh_hl_meta(),
        momentum_meta={"momentum": "POSITIVE"},
    )
    setup = eng.detect(snap)[0]
    # Force a terminal state, then a later snapshot should not advance it.
    setup.state = SetupState.INVALIDATED
    later = snapshot(regime=Regime.BULL, trend_meta=bull_meta(), structure_meta=hh_hl_meta(),
                     momentum_meta={"momentum": "POSITIVE"})
    assert eng.advance(setup, later) is None


def test_persistence_create_and_events(settings, session_factory):
    from veyra.database.setup_repository import SetupRepository

    repo = SetupRepository(session_factory)
    eng = _engine(settings)
    snap = snapshot(
        regime=Regime.BULL,
        trend_meta=bull_meta(),
        structure_meta=hh_hl_meta(),
        momentum_meta={"momentum": "POSITIVE"},
    )
    setup = eng.detect(snap)[0]
    row = repo.create(setup)
    assert row.id is not None
    assert row.state == "DETECTED"
    events = repo.events_for(row.id)
    assert len(events) == 1
    assert events[0].event_type == "DETECTED"
    # Advance + persist an event.
    later = snapshot(regime=Regime.BULL, trend_meta=bull_meta(), structure_meta=hh_hl_meta(),
                     momentum_meta={"momentum": "POSITIVE"},
                     timestamp=1_000_000_000 + 14400)
    new_state = eng.advance(setup, later)
    if new_state is not None:
        repo.update_state(row.id, setup, setup.state.value)
        events = repo.events_for(row.id)
        assert events[-1].event_type == setup.state.value


def test_end_to_end_candles_to_persisted_setup(settings, session_factory):
    """Candles -> pipeline snapshot -> SetupEngine -> scored -> persisted."""
    from veyra.market.pipeline import MarketAnalysisPipeline
    from veyra.database.setup_repository import SetupRepository
    from .market_synth import bull_continuation_frame

    data = bull_continuation_frame(n=300, start_ts=1_000_000_000)
    pipeline = MarketAnalysisPipeline.default(settings)
    snap = pipeline.analyze("BTC/USDT", "4H", data)

    eng = _engine(settings)
    setups = eng.detect(snap)
    # The synthetic uptrend with HH_HL structure should yield setups.
    assert setups
    for s in setups:
        assert s.evidence
        assert s.overall_score >= 0

    repo = SetupRepository(session_factory)
    live = repo.find_live("BTC/USDT", "4H")
    # Nothing persisted yet.
    assert live == []
    first = setups[0]
    row = repo.create(first)
    live = repo.find_live("BTC/USDT", "4H")
    assert len(live) == 1
    assert live[0].state == first.state.value