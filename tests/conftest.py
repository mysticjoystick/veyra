"""Shared pytest fixtures for Veyra.

All test data is isolated to a temporary directory so tests never touch
real data/ directories.
"""

from __future__ import annotations

import time

import pandas as pd
import pytest

from veyra.config import Settings
from veyra.data.candle_store import CandleStore
from veyra.database.engine import build_engine, init_db, make_session_factory
from veyra.database.dataset_repository import DatasetRepository
from veyra.domain.candle import Candle


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        candle_store_dir=tmp_path / "data" / "candles",
        database_url=f"sqlite:///{tmp_path / 'data' / 'test.db'}",
        environment="test",
    )


@pytest.fixture
def candle_store(settings) -> CandleStore:
    return CandleStore(settings)


@pytest.fixture
def session_factory(settings):
    engine = build_engine(settings)
    init_db(engine)
    return make_session_factory(engine)


@pytest.fixture
def dataset_repo(session_factory) -> DatasetRepository:
    return DatasetRepository(session_factory)


def make_candles(
    symbol: str = "BTC/USDT",
    timeframe: str = "4H",
    n: int = 50,
    start_ts: int | None = None,
    base: float = 100.0,
) -> list[Candle]:
    """Generate a deterministic ascending series of valid candles."""
    if start_ts is None:
        start_ts = int(time.time()) - n * 14400
    candles = []
    for i in range(n):
        ts = start_ts + i * 14400
        o = base + i
        c = base + i + 0.5
        candles.append(
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                open_time=ts,
                open=o,
                high=max(o, c) + 1.0,
                low=min(o, c) - 1.0,
                close=c,
                volume=1000.0 + i,
            )
        )
    return candles


def candles_to_frame(candles: list[Candle]) -> pd.DataFrame:
    df = pd.DataFrame([c.to_dict() for c in candles])
    df = df[["open_time", "open", "high", "low", "close", "volume"]]
    return df.sort_values("open_time").reset_index(drop=True)
