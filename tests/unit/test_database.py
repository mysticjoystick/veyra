"""Tests for database bootstrap and ORM persistence."""

from __future__ import annotations

from veyra.database.engine import build_engine, init_db, make_session_factory
from veyra.database.models import Candle, MarketSnapshot, Setup
from veyra.domain import Regime, SetupState, SetupType, MarketSide


def test_db_engine_initializes_and_stores(settings):
    engine = build_engine(settings)
    init_db(engine)
    Factory = make_session_factory(engine)

    with Factory() as session:
        candle = Candle(
            symbol="BTC/USDT",
            timeframe="4H",
            open_time=12345,
            open=100.0,
            high=105.0,
            low=99.0,
            close=102.0,
            volume=1500.0,
        )
        session.add(candle)
        session.commit()

    with Factory() as session:
        row = session.query(Candle).filter_by(symbol="BTC/USDT").one()
        assert row.close == 102.0


def test_db_persists_setup_with_state(settings):
    engine = build_engine(settings)
    init_db(engine)
    Factory = make_session_factory(engine)

    with Factory() as session:
        setup = Setup(
            symbol="BTC/USDT",
            timeframe="4H",
            timestamp=12345,
            setup_type=SetupType.PULLBACK.value,
            side=MarketSide.LONG.value,
            regime=Regime.BULL.value,
            overall_score=84,
            state=SetupState.QUALIFIED.value,
        )
        session.add(setup)
        session.commit()

    with Factory() as session:
        stored = session.query(Setup).one()
        assert stored.state == "QUALIFIED"
        assert stored.overall_score == 84


def test_db_invalidates_cleanly_on_reload(settings):
    engine = build_engine(settings)
    init_db(engine)
    Factory = make_session_factory(engine)

    with Factory() as session:
        session.add(
            MarketSnapshot(
                symbol="ETH/USDT",
                timeframe="1D",
                timestamp=1,
                regime="RANGE",
                overall_score=57,
                system_state="WAIT",
            )
        )
        session.commit()

    with Factory() as session:
        snap = session.query(MarketSnapshot).one()
        assert snap.regime == "RANGE"
        assert snap.overall_score == 57