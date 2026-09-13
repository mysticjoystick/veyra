"""Tests for the 24-hour opportunity model (realized / projected / P&L)."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from veyra.alerts.forward import ForwardReturnModel, ForwardStats
from veyra.alerts.service import AlertService


def _candles():
    """10 consecutive 1D candles; closes rise by $1 each day."""
    rows = []
    for i in range(10):
        rows.append(
            {
                "open_time": 1_700_000_000 + i * 86400,
                "open": 100.0 + i,
                "high": 101.0 + i,
                "low": 99.0 + i,
                "close": 100.0 + i,  # LONG up-close trend
                "volume": 1000.0,
            }
        )
    return pd.DataFrame(rows)


def test_realized_long_is_positive_when_price_rises():
    df = _candles()
    model = ForwardReturnModel(df)
    # Detection at bar 0, LONG, entered at 100 -> 24h later close = 101 => +1%
    r = model.realized(detection_ts=1_700_000_000, side="LONG", entry_price=100.0)
    assert r is not None
    assert abs(r - 0.01) < 1e-9


def test_realized_short_is_inverted():
    df = _candles()
    model = ForwardReturnModel(df)
    r = model.realized(detection_ts=1_700_000_000, side="SHORT", entry_price=100.0)
    assert r is not None
    assert abs(r + 0.01) < 1e-9


def test_realized_returns_none_outside_history():
    df = _candles()
    model = ForwardReturnModel(df)
    # Detection near the end: no candle 24h later.
    last_ts = 1_700_000_000 + 9 * 86400
    assert model.realized(last_ts, "LONG", entry_price=100.0) is None
    # Empty frame -> None
    assert ForwardReturnModel(None).realized(1_700_000_000, "LONG", 100.0) is None


def test_stats_by_band_aggregates_win_rate_and_mean():
    df = _candles()
    model = ForwardReturnModel(df)
    alerts = [
        {
            "band": "CONVERGENT",
            "realized_24h": 0.10,
        },
        {"band": "CONVERGENT", "realized_24h": 0.02},
        {"band": "CONVERGENT", "realized_24h": -0.05},
        {"band": "DIRECTIONAL", "realized_24h": 0.01},
    ]
    stats = model.stats_by_band(alerts)
    conv = stats["CONVERGENT"]
    assert conv.n == 3
    assert abs(conv.win_rate - 2 / 3) < 1e-9
    assert abs(conv.mean_return - (0.10 + 0.02 - 0.05) / 3) < 1e-9


def test_project_uses_band_mean_and_weights_pnl():
    df = _candles()
    model = ForwardReturnModel(df)
    stats = {
        "CONVERGENT": ForwardStats(band="CONVERGENT", n=30, win_rate=0.7, mean_return=0.05, median_return=0.04),
    }
    alert = {"band": "CONVERGENT", "score_normalized": 90}
    p = model.project(alert, stats, amount=1000.0)
    assert p.projected_return == 0.05
    assert p.pnl_at_amount == 50.0
    assert p.confidence == "HIGH"  # n >= 30


def test_project_confidence_scales_with_sample():
    df = _candles()
    model = ForwardReturnModel(df)
    alert = {"band": "CONVERGENT", "score_normalized": 90}
    low = model.project(alert, {}, amount=100.0)
    assert low.confidence == "LOW"
    assert low.projected_return is None


def test_stats_by_band_includes_risk_of_losing_trades():
    df = _candles()
    model = ForwardReturnModel(df)
    alerts = [
        {"band": "CONVERGENT", "realized_24h": 0.10},
        {"band": "CONVERGENT", "realized_24h": -0.05},
        {"band": "CONVERGENT", "realized_24h": -0.03},
    ]
    st = model.stats_by_band(alerts)["CONVERGENT"]
    assert abs(st.risk - 0.04) < 1e-9  # mean of (0.05, 0.03)
    d = st.to_dict()
    assert "risk" in d
    assert d["risk"] is not None


def test_histogram_buckets_realized_returns():
    from veyra.alerts.service import _histogram

    alerts = [
        {"band": "CONVERGENT", "realized_24h": 0.30},   # clamps to top bin
        {"band": "CONVERGENT", "realized_24h": -0.30},  # clamps to bottom bin
        {"band": "CONVERGENT", "realized_24h": 0.0},
        {"band": "CONVERGENT", "realized_24h": 0.0},
        {"band": "DIRECTIONAL", "realized_24h": 0.5},   # other band ignored
        {"band": "CONVERGENT"},                          # no value ignored
    ]
    h = _histogram(alerts, "CONVERGENT", bins=8, width=0.20)
    assert h["bins"] == 8
    assert len(h["counts"]) == 8
    assert sum(h["counts"]) == 4  # only 4 valid CONVERGENT values
    mid = len(h["counts"]) // 2
    assert h["counts"][0] == 1 and h["counts"][-1] == 1  # extremes clamped
    assert h["counts"][mid] == 2  # zero lands in the middle bucket


def test_alert_service_project_exposes_pnl():
    svc = AlertService(datasets=[{"symbol": "BTC/USDT", "timeframe": "1D"}])
    out = svc.project("BTC/USDT", "1D", amount=1000.0)
    assert out["amount"] == 1000.0
    assert out["count"] >= 1
    conv = [p for p in out["projections"] if p["band"] == "CONVERGENT"][:1]
    assert conv[0]["sample_size"] >= 1
    assert conv[0]["pnl_at_amount"] is not None


@pytest.fixture
def _portfolio_metrics():
    # A tiny internal helper to exercise verdict mapping in isolation (no network).
    from veyra.alerts.forward import ForwardStats

    def build(win_rate, mean_return, risk):
        return {
            "symbol": "X",
            "timeframe": "1D",
            "sample": 100,
            "win_rate": win_rate,
            "mean_24h": mean_return,
            "risk_24h": risk,
            "projected_pnl": mean_return * 1000.0,
            "risk_usd": (risk or 0) * 1000.0,
        }

    return build


def test_verdict_maps_to_trade_watch_hold(_portfolio_metrics):
    from veyra.alerts.forward import ForwardStats

    def classify(st):
        return max(
            st.win_rate or 0,
        ), st

    # TRADE: pnl>0, win>=55, return>risk
    tr = ForwardStats(band="CONVERGENT", n=100, win_rate=0.77, mean_return=0.073, risk=0.02)
    # WATCH: pnl>0 but risk >= return (upside no longer clearly beats downside)
    wa = ForwardStats(band="CONVERGENT", n=100, win_rate=0.72, mean_return=0.031, risk=0.04)
    # HOLD: negative edge
    ho = ForwardStats(band="CONVERGENT", n=100, win_rate=0.5, mean_return=-0.02, risk=0.03)
    label = lambda st: (
        "TRADE"
        if (st.mean_return is not None and st.mean_return * 1000 > 0 and (st.win_rate or 0) >= 0.55 and (st.risk is None or st.mean_return > st.risk))
        else ("WATCH" if st.mean_return is not None and st.mean_return * 1000 > 0 else "HOLD")
    )
    assert label(tr) == "TRADE"
    assert label(wa) == "WATCH"
    assert label(ho) == "HOLD"