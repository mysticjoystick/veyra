"""Tests for the keyless Binance WebSocket kline stream helpers.

Network sockets are not exercised; these verify the pure parsing/state
logic that maps raw stream payloads onto an in-memory candle cache.
"""

from __future__ import annotations

from veyra.data.provider.binance_stream import (
    BinanceStream,
    _key,
    _key_from_stream,
    _parse_stream,
)


def test_parse_stream_json():
    payload = '{"stream":"btcusdt@kline_4h","data":{"k":{"t":1700000000000,"T":1700014400000,"o":"100","h":"110","l":"95","c":"108","v":"12.5","x":false}}}'
    msg = _parse_stream(payload.encode())
    assert msg["stream"] == "btcusdt@kline_4h"
    assert msg["data"]["k"]["x"] is False


def test_parse_stream_ignores_binary_garbage():
    assert _parse_stream(b"\x00garbage") is None
    assert _parse_stream(None) is None
    assert _parse_stream("not json") is None


def test_key_mapping_normalizes_symbol_and_interval():
    assert _key("BTC/USDT", "4H") == ("BTCUSDT", "4h")
    assert _key("eth/usdt", "1D") == ("ETHUSDT", "1d")


def test_key_from_stream_parses_symbol_and_interval():
    assert _key_from_stream("btcusdt@kline_4h") == ("BTCUSDT", "4h")
    assert _key_from_stream("ETHUSDT@kline_1d") == ("ETHUSDT", "1d")
    assert _key_from_stream("malformed") is None


def test_handle_populates_candle_cache():
    stream = BinanceStream()
    payload = {
        "stream": "btcusdt@kline_4h",
        "data": {
            "k": {
                "t": 1_700_000_000_000,
                "T": 1_700_014_399_000,
                "o": "100",
                "h": "110",
                "l": "95",
                "c": "108",
                "v": "12.5",
                "x": True,
            }
        },
    }
    stream._handle(_parse_stream(__import__("json").dumps(payload)))
    candle = stream.candle("BTC/USDT", "4H")
    assert candle is not None
    assert candle["open_time"] == 1_700_000_000
    assert candle["close"] == 108.0
    assert candle["is_closed"] is True


def test_handle_ignores_non_kline_streams():
    stream = BinanceStream()
    stream._handle({"stream": "btcusdt@ticker", "data": {}})
    assert stream.candle("BTC/USDT", "4H") is None


def test_candle_missing_returns_none():
    stream = BinanceStream()
    assert stream.candle("BTC/USDT", "4H") is None