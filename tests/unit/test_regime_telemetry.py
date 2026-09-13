"""Tests for regime-transition telemetry."""

import json

from veyra.market.regime_telemetry import RegimeTelemetry


def test_first_observation_does_not_write_line(tmp_path):
    tel = RegimeTelemetry(tmp_path / "regime.jsonl")
    row = tel.observe("BTC/USDT", "4H", "BULL", {"trend": "BULLISH"}, ts=1000)
    assert row is None
    assert not (tmp_path / "regime.jsonl").exists()
    assert tel.last_regime("BTC/USDT", "4H") == "BULL"


def test_transition_writes_row_with_evidence(tmp_path):
    tel = RegimeTelemetry(tmp_path / "regime.jsonl")
    tel.observe("BTC/USDT", "4H", "BULL", {"trend": "BULLISH"}, ts=1000)
    row = tel.observe(
        "BTC/USDT", "4H", "RANGE",
        {"trend": "NEUTRAL", "structure": "MIXED", "volatility": "NORMAL"}, ts=2000,
    )
    assert row is not None
    assert row["from"] == "BULL"
    assert row["to"] == "RANGE"
    assert row["ts"] == 2000
    assert row["evidence"]["trend"] == "NEUTRAL"
    assert tel.last_regime("BTC/USDT", "4H") == "RANGE"


def test_repeated_regime_is_noop(tmp_path):
    tel = RegimeTelemetry(tmp_path / "regime.jsonl")
    tel.observe("BTC/USDT", "4H", "BULL", ts=1000)
    assert tel.observe("BTC/USDT", "4H", "BULL", ts=1500) is None
    assert len(tel.transitions()) == 0


def test_markets_are_independent(tmp_path):
    tel = RegimeTelemetry(tmp_path / "regime.jsonl")
    tel.observe("BTC/USDT", "4H", "BULL", ts=1000)
    tel.observe("ETH/USDT", "4H", "BEAR", ts=1000)
    tel.observe("BTC/USDT", "4H", "RANGE", ts=2000)
    tel.observe("ETH/USDT", "4H", "BULL", ts=2000)
    assert len(tel.transitions()) == 2
    assert len(tel.transitions(symbol="BTC/USDT")) == 1
    assert tel.transitions(symbol="BTC/USDT")[0]["to"] == "RANGE"


def test_rehydrates_last_regime_on_restart(tmp_path):
    path = tmp_path / "regime.jsonl"
    tel = RegimeTelemetry(path)
    tel.observe("BTC/USDT", "4H", "BULL", ts=1000)
    tel.observe("BTC/USDT", "4H", "RANGE", ts=2000)

    fresh = RegimeTelemetry(path)
    assert fresh.last_regime("BTC/USDT", "4H") == "RANGE"
    # Same regime after restart: still no phantom transition.
    assert fresh.observe("BTC/USDT", "4H", "RANGE", ts=3000) is None


def test_skips_corrupted_lines(tmp_path):
    path = tmp_path / "regime.jsonl"
    tel = RegimeTelemetry(path)
    tel.observe("BTC/USDT", "4H", "BULL", ts=1000)
    tel.observe("BTC/USDT", "4H", "RANGE", ts=2000)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{not json}\n")
    assert len(tel.transitions()) == 1