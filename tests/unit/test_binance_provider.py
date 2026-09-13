"""Tests for the Binance provider (isolated; no live network)."""

from __future__ import annotations

import pytest

from veyra.data.provider.binance import BinanceProvider
from veyra.error import ProviderRequestError, RateLimitError


def _kline(open_time_s, size=1):
    """Build a minimal Binance kline row for a given open_time (seconds)."""
    o = 100.0 + open_time_s % 10
    return [
        open_time_s * 1000,  # openTime ms
        o,                   # open
        o + size,            # high
        o - size,            # low
        o + 0.5,             # close
        1000.0,              # volume
    ]


class _FakeHttp:
    """Injects scripted payloads into BinanceProvider fetch calls."""

    def __init__(self, pages):
        self._pages = list(pages)
        self.calls = 0

    def __call__(self, url, **kwargs):
        self.calls += 1
        page = self._pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page


def test_parse_klines_single_page():
    prov = BinanceProvider(limit=1000)
    payload = [_kline(1000), _kline(10400)]
    candles = prov._parse_klines(payload)
    assert len(candles) == 2
    # timestamps converted to seconds
    assert candles[0].open_time == 1000
    assert candles[1].open_time == 10400
    # provider fills symbol/timeframe via service; here empty but numeric OK
    assert candles[0].volume == 1000.0


def test_parse_klines_malformed_row_raises():
    prov = BinanceProvider()
    with pytest.raises(ProviderRequestError):
        prov._parse_klines([["not", "a", "kline"]])


def test_parse_klines_non_list_raises():
    prov = BinanceProvider()
    with pytest.raises(ProviderRequestError):
        prov._parse_klines({"error": "boom"})


def test_get_ohlcv_paginates_to_end_of_history():
    # 1500 candles in 3 pages of limit 1000 -> stop when page shorter.
    pages = [
        [_kline(t) for t in range(1000, 1000 + 1000 * 4, 4)],   # page 1 (1000)
        [_kline(t) for t in range(1000 + 4000, 1000 + 8 * 1000, 4)],  # page 2
        [_kline(t) for t in range(1000 + 8000, 1000 + 9 * 1000, 4)],  # page 3 short
    ]
    # Simpler: just feed two full pages then a short page.
    fake = _FakeHttp(
        [
            [_kline(1000 + i * 4) for i in range(1000)],
            [_kline(1000 + 4000 + i * 4) for i in range(1000)],
            [_kline(1000 + 9000 + i * 4) for i in range(10)],
        ]
    )
    prov = BinanceProvider(limit=1000)
    prov._http_get_json = fake
    candles = prov.get_ohlcv("BTC/USDT", "4H", start_time=1000)
    assert len(candles) == 1000 + 1000 + 10  # not an infinite loop
    assert candles[0].open_time == 1000
    assert candles[-1].open_time == 1000 + 9000 + 9 * 4


def test_get_ohlcv_stops_at_end_time():
    pages = [
        [_kline(1000 + i * 4) for i in range(500)],  # reaches last_ts 1000+4*499
    ]
    fake = _FakeHttp(pages)
    prov = BinanceProvider(limit=1000)
    prov._http_get_json = fake
    candles = prov.get_ohlcv("BTC/USDT", "4H", start_time=1000, end_time=1000 + 500 * 4 - 4)
    assert len(candles) == 500
    assert candles[-1].open_time <= 1000 + 500 * 4 - 4


def test_empty_response_returns_empty():
    fake = _FakeHttp([[]])
    prov = BinanceProvider()
    prov._http_get_json = fake
    assert prov.get_ohlcv("BTC/USDT", "4H", start_time=1000) == []


def test_rate_limit_retries_then_succeeds():
    attempts = {
        "ok": [_kline(1000)],
        "rl": RateLimitError("429")
    }
    fake = _FakeHttp([attempts["rl"], attempts["rl"], attempts["ok"]])
    prov = BinanceProvider(max_retries=3, base_backoff_seconds=0.0)
    prov._http_get_json = fake
    candles = prov.get_ohlcv("BTC/USDT", "4H", start_time=1000)
    assert len(candles) == 1
    assert fake.calls == 3


def test_rate_limit_exhausted_raises():
    fake = _FakeHttp([RateLimitError("429")])
    prov = BinanceProvider(max_retries=1, base_backoff_seconds=0.0)
    prov._http_get_json = fake
    with pytest.raises(RateLimitError):
        prov.get_ohlcv("BTC/USDT", "4H", start_time=1000)


def test_network_error_retries_then_raises():
    from urllib.error import URLError

    fake = _FakeHttp([URLError("timeout"), URLError("timeout"), URLError("timeout")])
    prov = BinanceProvider(max_retries=2, base_backoff_seconds=0.0)
    prov._http_get_json = fake
    with pytest.raises(ProviderRequestError):
        prov.get_ohlcv("BTC/USDT", "4H", start_time=1000)


def test_get_realtime_price_returns_last_price(monkeypatch):
    prov = BinanceProvider()
    fake = _FakeHttp([{"lastPrice": "67500.0", "symbol": "BTCUSDT"}])
    prov._http_get_json = fake
    assert prov.get_realtime_price("BTC/USDT") == 67500.0


def test_get_realtime_price_tolerates_network_error(monkeypatch):
    from urllib.error import URLError

    prov = BinanceProvider()
    fake = _FakeHttp([URLError("offline")])
    prov._http_get_json = fake
    assert prov.get_realtime_price("BTC/USDT") is None


def test_get_realtime_price_tolerates_malformed_payload(monkeypatch):
    prov = BinanceProvider()
    fake = _FakeHttp([{"noLastPrice": "1"}])
    prov._http_get_json = fake
    assert prov.get_realtime_price("BTC/USDT") is None
