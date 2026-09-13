"""Phase 5 STEP 2 tests: real-data retrieval + provenance manifests.

Uses the FakeProvider and a tmp CandleStore so the tests never hit the
network. They pin the integrity-reporting contract Phase 5 builds on:
honest VALID / VALID_WITH_GAPS / INVALID status, gap/duplicate detection,
and reproducible manifests with content hashes.
"""

from __future__ import annotations

import json

import pytest

from veyra.config import Settings
from veyra.data.candle_store import CandleStore
from veyra.domain.candle import Candle
from veyra.realdata import DataQualityReport, RealDataLoader

from .fake_provider import FakeProvider
from .helpers import make_candles


@pytest.fixture
def settings(tmp_path):
    return Settings(
        data_dir=tmp_path / "data",
        candle_store_dir=tmp_path / "data" / "candles",
    )


def _loader(settings, provider, end_ts=None) -> RealDataLoader:
    return RealDataLoader(
        settings=settings,
        provider=provider,
        store=CandleStore(settings),
        manifest_dir=settings.absolute_data_dir / "manifests",
        start_ts=1_000_000,
        end_ts=end_ts,
    )


def test_full_series_is_valid_and_writes_manifest(settings):
    provider = FakeProvider(make_candles("BTC/USDT", "4H", n=40, start_ts=1_000_000))
    m = _loader(settings, provider).ingest_one("BTC/USDT", "4H")

    assert m["quality"]["status"] == "VALID"
    assert m["candle_count"] == 40
    assert m["quality"]["gap_count"] == 0
    assert m["quality"]["duplicate_count"] == 0
    assert m["provider"] == "fake"
    assert m["content_hash"]

    stored = CandleStore(settings).load("BTC/USDT", "4H")
    assert len(stored) == 40

    path = settings.absolute_data_dir / "manifests" / "BTC_USDT_4H.json"
    assert path.exists()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["candle_count"] == 40


def test_gap_series_is_valid_with_gaps(settings):
    # Drop one candle in the middle to create a genuine gap.
    base = make_candles("ETH/USDT", "4H", n=40, start_ts=1_000_000)
    dropped = [c for c in base if c.open_time != 1_000_000 + 10 * 14400]
    provider = FakeProvider(dropped)
    m = _loader(settings, provider).ingest_one("ETH/USDT", "4H")

    assert m["candle_count"] == 39
    assert m["quality"]["gap_count"] == 1
    assert m["quality"]["status"] == "VALID_WITH_GAPS"


def test_duplicate_timestamps_are_detected(settings):
    base = make_candles("BTC/USDT", "4H", n=20, start_ts=1_000_000)
    # Insert a duplicate adjacent to its original so the series stays
    # chronological; only the duplicate flag should fire (a duplicate is a
    # warning, not a fatal ordering/corruption error).
    dup = list(base)
    dup.insert(6, base[5])
    q = DataQualityReport(dup, 14400)
    assert q.duplicate_count == 1
    assert q.chronological is True
    assert q.status == "VALID"  # duplicates are flagged but not fatal


def test_reversed_series_is_invalid(settings):
    from veyra.realdata import DataQualityReport

    base = make_candles("BTC/USDT", "4H", n=20, start_ts=1_000_000)
    q = DataQualityReport(list(reversed(base)), 14400)
    assert q.chronological is False
    assert q.status == "INVALID"


def test_invalid_ohlc_is_invalid(settings):
    base = make_candles("BTC/USDT", "4H", n=20, start_ts=1_000_000)
    bad = [
        Candle(
            symbol=c.symbol,
            timeframe=c.timeframe,
            open_time=c.open_time,
            open=c.open,
            high=c.high,
            low=c.low + 100,  # high < low violation
            close=c.close,
            volume=c.volume,
        )
        for c in base
    ]
    q = DataQualityReport(bad, 14400)
    assert q.invalid_ohlcv >= 1
    assert q.status == "INVALID"