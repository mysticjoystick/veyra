"""Tests for the Step-3 realtime paper checkpoint engine (simulation only)."""

from __future__ import annotations

import json

import pytest

from veyra.alerts.costs import CostModel
from veyra.paper.live import (
    LivePaperLedger,
    LivePaperPosition,
    _band_for,
    _cold_scale,
    _is_trade_quality,
    _next_open_or_area,
    _stop_from_setup,
)


def _stats(mean=0.06, wr=0.7, risk=0.02, n=40):
    return type("S", (), {"n": n, "win_rate": wr, "mean_return": mean, "risk": risk})()


def test_band_for_maps_score():
    assert _band_for(90) == "CONVERGENT"
    assert _band_for(60) == "DIRECTIONAL"
    assert _band_for(40) == "EMERGENT"
    assert _band_for(10) == "SCANNED"


def test_trade_quality_requires_pnl_wr_and_mean_gt_risk():
    assert _is_trade_quality(_stats(mean=0.06, wr=0.7, risk=0.02))
    assert not _is_trade_quality(_stats(mean=-0.02, wr=0.7, risk=0.02))  # losing
    assert not _is_trade_quality(_stats(mean=0.06, wr=0.5, risk=0.02))  # wr too low
    assert not _is_trade_quality(_stats(mean=0.01, wr=0.7, risk=0.02))  # mean <= risk
    assert not _is_trade_quality(_stats(n=0))  # no sample


def test_position_unrealized_pnl_honours_side():
    long = LivePaperPosition("BTC/USDT", "1D", "LONG", "CONVERGENT", 100, 100.0, 1000.0)
    long.mark_price = 110.0
    assert long.unrealized_pnl == pytest.approx(100.0)
    short = LivePaperPosition("BTC/USDT", "1D", "SHORT", "CONVERGENT", 100, 100.0, 1000.0)
    short.mark_price = 90.0
    assert short.unrealized_pnl == pytest.approx(100.0)
    assert long.to_dict()["unrealized_pnl"] == pytest.approx(100.0)


def test_ledger_round_trips_via_to_dict_and_position_serialization():
    from veyra.paper.live import LivePaperEngine

    rebuild = LivePaperEngine._position_from_dict

    pos = LivePaperPosition("BTC/USDT", "1D", "LONG", "CONVERGENT", 100, 100.0,
                            1000.0, status="closed", projected_return=0.05,
                            exit_ts=200, exit_price=105.0, gross_return=0.05,
                            fees=3.0, net_return=0.047, pnl=47.0, exit_reason="horizon")
    d = pos.to_dict()
    assert d["exit_reason"] == "horizon"
    assert d["net_return"] == pytest.approx(0.047)
    rebuilt = rebuild(d)
    assert rebuilt.side == "LONG"
    assert rebuilt.status == "closed"
    assert rebuilt.net_return == pytest.approx(0.047)
    assert rebuilt.exit_reason == "horizon"


def test_net_costs_applied_on_close():
    from veyra.paper.live import LivePaperEngine

    engine = LivePaperEngine.__new__(LivePaperEngine)
    engine._cost = CostModel()
    pos = LivePaperPosition("BTC/USDT", "1D", "LONG", "CONVERGENT", 100, 100.0, 1000.0)
    engine._close_position(pos, 105.0, 200, "horizon")
    assert pos.status == "closed"
    assert pos.gross_return == pytest.approx(0.05)
    # 0.30% round-trip on $1000 = $3.00 fees, net 4.7%.
    assert pos.fees == pytest.approx(3.0)
    assert pos.net_return == pytest.approx(0.047)
    assert pos.pnl == pytest.approx(47.0)
    assert pos.exit_reason == "horizon"


def test_next_open_or_area_prefers_next_bar_open():
    class FakeRow(dict):
        def __init__(self, open_price):
            super().__init__({"open": open_price})

    class FakeModel:
        def _row_at_or_after(self, ts):
            return FakeRow(200.0)

    setup = {"timestamp": 100, "time": "1D", "interest_area": {"low": 150.0, "high": 160.0}}
    assert _next_open_or_area(setup, FakeModel()) == pytest.approx(200.0)


def test_next_open_or_area_falls_back_to_area():
    class EmptyModel:
        def _row_at_or_after(self, ts):
            return None

    setup = {"timestamp": 100, "time": "1D", "interest_area": {"low": 150.0, "high": 160.0}}
    assert _next_open_or_area(setup, EmptyModel()) == pytest.approx(155.0)


def test_report_summarizes_closed_trades(tmp_path):
    from veyra.paper.live import LivePaperEngine
    eng = LivePaperEngine.__new__(LivePaperEngine)
    eng._ledger_dir = tmp_path
    eng._cost = CostModel()
    ledger = LivePaperLedger(symbol="BTC/USDT", timeframe="4H", strategy_version="veyra-live-v1")
    win = LivePaperPosition("BTC/USDT", "4H", "LONG", "CONVERGENT", 100, 100.0,
                            1000.0, status="closed", projected_pnl=50.0,
                            exit_ts=200, exit_price=110.0, gross_return=0.10,
                            fees=3.0, net_return=0.097, pnl=97.0, exit_reason="horizon")
    loss = LivePaperPosition("BTC/USDT", "4H", "SHORT", "CONVERGENT", 300, 100.0,
                             1000.0, status="closed", projected_pnl=-20.0,
                             exit_ts=400, exit_price=105.0, gross_return=-0.05,
                             fees=3.0, net_return=-0.053, pnl=-53.0, exit_reason="horizon")
    ledger.trades = [win, loss]
    eng._persist(ledger)

    r = eng.report("BTC/USDT", "4H")
    assert r["simulation"] is True
    assert r["trades"] == 2
    assert r["wins"] == 1
    assert r["win_rate"] == pytest.approx(0.5)
    assert r["sum_pnl"] == pytest.approx(44.0)
    assert r["sum_projected"] == pytest.approx(30.0)
    assert "simulation only" in r["note"]


def _fake_live(setup_score=90, side="LONG", ts=1_700_000_000, timeframe="4H"):
    return {
        "setups": [
            {
                "setup_type": "TREND_CONTINUATION", "side": side,
                "regime": "BULL", "overall_score": setup_score, "timestamp": ts,
                "time": timeframe,
                "interest_area": {"low": 100.0, "high": 110.0},
                "invalidation": 90.0, "reasoning": "t",
            }
        ],
        "live_price": 108.0,
    }


def _candle_frame(closes, step=14400):
    """Candles ascending by `step` seconds; closes supplied, open=close."""
    rows = []
    start = 1_700_000_000
    for i, c in enumerate(closes):
        rows.append({
            "open_time": start + i * step, "open": c, "high": c + 1.0,
            "low": c - 1.0, "close": c, "volume": 1000.0,
        })
    return __import__("pandas").DataFrame(rows)


def test_checkpoint_opens_then_settles_at_horizon(tmp_path, monkeypatch):
    from veyra.paper.live import LivePaperEngine

    entry_ts = 1_700_000_000
    # 4H candles covering a detection and well past +24h, LONG up-move.
    closes = [100.0 + 2.0 * i for i in range(40)]
    df = _candle_frame(closes, step=14400)

    class FakeStore:
        def load(self, symbol, timeframe):
            return df

    class FakeScan:
        _store = FakeStore()

    class FakeEngine(LivePaperEngine):
        def _market_alerts(self, symbol, timeframe):
            # 40 observations, 30 wins (0.75): the posterior passes the gate.
            return [{"band": "CONVERGENT", "net_24h": x}
                    for x in (0.06, 0.04, 0.05, -0.01) * 10]

    eng = FakeEngine(ledger_dir=tmp_path, scan=FakeScan())
    eng._scan = FakeScan()

    # First checkpoint at the detection moment: opens a CONVERGENT LONG.
    ledger1 = eng.run_checkpoint("BTC/USDT", "4H", amount=1000.0, refresh=False,
                                 live=_fake_live(ts=entry_ts, timeframe="4H"))
    open_pos = ledger1.unclosed()
    assert len(open_pos) == 1
    pos = open_pos[0]
    assert pos.band == "CONVERGENT"
    assert pos.side == "LONG"
    assert pos.entry_ts == entry_ts
    assert pos.n_observed_at_decision_time == 40

    # Second checkpoint far past the 24h horizon settles the position.
    monkeypatch.setattr("veyra.paper.live.time.time",
                        lambda: entry_ts + 24 * 3600 + 60)
    # Reuse the same engine (its ledger is persisted under tmp_path) with an
    # already-open CONVERGENT position; supply a live payload placed before
    # the detection so it doesn't open a duplicate, and let settlement run.
    ledger2 = eng.run_checkpoint("BTC/USDT", "4H", amount=1000.0, refresh=False,
                                 live=_fake_live(ts=entry_ts - 24 * 3600, timeframe="4H"))
    closed = [t for t in ledger2.trades]
    assert len(closed) == 1
    assert closed[0].status == "closed"
    assert closed[0].exit_reason == "horizon"
    assert closed[0].net_return is not None and closed[0].net_return > 0


def test_stop_from_setup_prefers_invalidation_aligned_to_side():
    # LONG stops at/below invalidate; SHORT stops at/above invalidate.
    assert _stop_from_setup("LONG", 100.0, 94.0) == pytest.approx(94.0)
    assert _stop_from_setup("SHORT", 100.0, 106.0) == pytest.approx(106.0)


def test_stop_from_setup_falls_back_away_from_entry():
    long_stop = _stop_from_setup("LONG", 100.0, None)
    short_stop = _stop_from_setup("SHORT", 100.0, None)
    assert long_stop == pytest.approx(98.0)   # -2% for LONG
    assert short_stop == pytest.approx(102.0)  # +2% for SHORT


def test_stop_from_setup_is_volatility_aware():
    # ATR*2 (3.0) is wider than the structural 1% stop -> stop lands at 3%.
    assert _stop_from_setup("LONG", 100.0, 99.0, atr=1.5, atr_multiplier=2.0) == pytest.approx(97.0)
    # ATR*2 (1.0) is tighter than the structural 6% stop -> structural governs.
    assert _stop_from_setup("LONG", 100.0, 94.0, atr=0.5, atr_multiplier=2.0) == pytest.approx(94.0)
    # SHORT mirrored: widest of structural 1% and ATR*2 (8%) -> stop at +8%.
    assert _stop_from_setup("SHORT", 100.0, 101.0, atr=4.0, atr_multiplier=2.0) == pytest.approx(108.0)
    # Wrong-side invalidation (above entry for a LONG) is ignored, ATR governs.
    assert _stop_from_setup("LONG", 100.0, 105.0, atr=1.0, atr_multiplier=2.0) == pytest.approx(98.0)


def test_cold_scale_tiers_risk_by_observation_count():
    assert _cold_scale(None) == 0.25
    assert _cold_scale(0) == 0.25
    assert _cold_scale(19) == 0.25
    assert _cold_scale(20) == 0.50
    assert _cold_scale(49) == 0.50
    assert _cold_scale(50) == 1.00
    assert _cold_scale(200) == 1.00


def test_open_sizes_position_to_risk_budget(tmp_path):
    """A warm band (n=52) sizes its notional so the stop risks the full per-trade
    budget: risk_usd = 10 * 1.0; 10% stop -> notional 100."""
    import pandas as pd

    from veyra.paper.live import LivePaperEngine

    rows = []
    start = 1_700_000_000
    for i in range(40):
        rows.append({"open_time": start + i * 14400, "open": 100.0, "high": 101.0,
                     "low": 99.0, "close": 100.0, "volume": 1.0})
    df = pd.DataFrame(rows)

    class FakeStore:
        def load(self, s, t):
            return df

    class FakeScan:
        _store = FakeStore()

    class FakeEngine(LivePaperEngine):
        def _market_alerts(self, symbol, timeframe):
            return [{"band": "CONVERGENT", "net_24h": x}
                    for x in (0.06, 0.04, 0.05, -0.01) * 13]

    eng = FakeEngine(ledger_dir=tmp_path, scan=FakeScan())
    eng._scan = FakeScan()
    setup = {
        "setup_type": "TREND_CONTINUATION", "side": "LONG", "regime": "BULL",
        "overall_score": 90, "timestamp": start, "time": "4H",
        "interest_area": {"low": 99.0, "high": 101.0},
        "invalidation": 90.0, "reasoning": "t", "atr": None,
    }
    ledger = eng.run_checkpoint("BTC/USDT", "4H", amount=1000.0, refresh=False,
                                live={"setups": [setup], "live_price": 100.0})
    open_pos = ledger.unclosed()
    assert len(open_pos) == 1
    pos = open_pos[0]
    # entry = next bar open (100.0); structural stop at invalidation 90 (10%).
    assert pos.entry_price == pytest.approx(100.0)
    assert pos.stop_price == pytest.approx(90.0)
    # stop_pct = 0.10; risk_usd = 10 * 1.0 (warm, n=52) = 10 -> notional 100.
    assert pos.notional == pytest.approx(100.0)
    assert pos.n_observed_at_decision_time == 52
    assert pos.cold_start is False


def test_open_does_not_trigger_without_band_evidence(tmp_path):
    """Zero observed band history can never pass the posterior gate (the prior
    win-rate 0.50 < 0.55 floor) - the cold-start bypass is gone."""
    import pandas as pd

    from veyra.paper.live import LivePaperEngine

    rows = []
    start = 1_700_000_000
    for i in range(40):
        rows.append({"open_time": start + i * 14400, "open": 100.0, "high": 101.0,
                     "low": 99.0, "close": 100.0, "volume": 1.0})
    df = pd.DataFrame(rows)

    class FakeStore:
        def load(self, s, t):
            return df

    class FakeScan:
        _store = FakeStore()

    class FakeEngine(LivePaperEngine):
        def _market_alerts(self, symbol, timeframe):
            return []  # no observed evidence for any band

    eng = FakeEngine(ledger_dir=tmp_path, scan=FakeScan())
    eng._scan = FakeScan()
    setup = {
        "setup_type": "TREND_CONTINUATION", "side": "LONG", "regime": "BULL",
        "overall_score": 90, "timestamp": start, "time": "4H",
        "interest_area": {"low": 99.0, "high": 101.0},
        "invalidation": 90.0, "reasoning": "t",
    }
    ledger = eng.run_checkpoint("BTC/USDT", "4H", amount=1000.0, refresh=False,
                                live={"setups": [setup], "live_price": 100.0})
    assert len(ledger.unclosed()) == 0


def test_portfolio_exposure_cap_blocks_gate_passing_entry(tmp_path):
    """The portfolio layer is wired into run_checkpoint: with a tiny exposure cap
    a gate-passing setup is scaled to the remaining budget; when the book already
    sits at the cap it is rejected outright."""
    import pandas as pd

    from veyra.paper.live import LivePaperEngine
    from veyra.paper.portfolio_risk import PortfolioRiskPolicy

    rows = []
    start = 1_700_000_000
    for i in range(40):
        rows.append({"open_time": start + i * 14400, "open": 100.0, "high": 101.0,
                     "low": 99.0, "close": 100.0, "volume": 1.0})
    df = pd.DataFrame(rows)

    class FakeStore:
        def load(self, s, t):
            return df

    class FakeScan:
        _store = FakeStore()

    class FakeEngine(LivePaperEngine):
        def _market_alerts(self, symbol, timeframe):
            return [{"band": "CONVERGENT", "net_24h": x}
                    for x in (0.06, 0.04, 0.05, -0.01) * 13]

    eng = FakeEngine(ledger_dir=tmp_path, scan=FakeScan())
    eng._scan = FakeScan()
    # equity 100 * 20% = cap 20.
    tiny = type(
        "S", (),
        {"portfolio_max_exposure_pct": 0.20,
         "portfolio_correlation_threshold": 0.70,
         "portfolio_correlation_multiplier": 0.50,
         "portfolio_equity": 100.0},
    )()
    policy = PortfolioRiskPolicy(tiny)
    setup = {
        "setup_type": "TREND_CONTINUATION", "side": "LONG", "regime": "BULL",
        "overall_score": 90, "timestamp": start, "time": "4H",
        "interest_area": {"low": 99.0, "high": 101.0},
        "invalidation": 90.0, "reasoning": "t",
    }

    # Empty book: risk-sized notional 100 scales down to the remaining 20.
    ledger = eng.run_checkpoint(
        "BTC/USDT", "4H", amount=1000.0, refresh=False,
        live={"setups": [setup], "live_price": 100.0},
        portfolio=policy, portfolio_open=[], portfolio_correlations=None,
    )
    open_pos = ledger.unclosed()
    assert len(open_pos) == 1
    assert open_pos[0].notional == pytest.approx(20.0)

    # Book already at the 20 cap: a second market is rejected.
    ledger2 = eng.run_checkpoint(
        "ETH/USDT", "4H", amount=1000.0, refresh=False,
        live={"setups": [setup], "live_price": 100.0},
        portfolio=policy,
        portfolio_open=[{"symbol": "BTC/USDT", "timeframe": "4H",
                         "side": "LONG", "notional": 20.0}],
        portfolio_correlations=None,
    )
    assert len(ledger2.unclosed()) == 0


def test_open_sizes_position_from_fallback_stop_when_no_atr(tmp_path):
    """Non-numeric invalidation + no ATR -> 2% fallback stop; warm (n=52) risk
    budget 10 at a 2% stop sizes the position to the full 500 budget."""
    import pandas as pd

    from veyra.paper.live import LivePaperEngine

    rows = []
    start = 1_700_000_000
    for i in range(40):
        rows.append({"open_time": start + i * 14400, "open": 100.0, "high": 101.0,
                     "low": 99.0, "close": 100.0, "volume": 1.0})
    df = pd.DataFrame(rows)

    class FakeStore:
        def load(self, s, t):
            return df

    class FakeScan:
        _store = FakeStore()

    class FakeEngine(LivePaperEngine):
        def _market_alerts(self, symbol, timeframe):
            return [{"band": "CONVERGENT", "net_24h": x}
                    for x in (0.06, 0.04, 0.05, -0.01) * 13]

    eng = FakeEngine(ledger_dir=tmp_path, scan=FakeScan())
    eng._scan = FakeScan()
    setup = {
        "setup_type": "TREND_CONTINUATION", "side": "LONG", "regime": "BULL",
        "overall_score": 90, "timestamp": start, "time": "4H",
        "interest_area": {"low": 99.0, "high": 101.0},
        "invalidation": "Loses structure below 98", "reasoning": "t",
    }
    ledger = eng.run_checkpoint("BTC/USDT", "4H", amount=1000.0, refresh=False,
                                live={"setups": [setup], "live_price": 100.0})
    open_pos = ledger.unclosed()
    assert len(open_pos) == 1
    pos = open_pos[0]
    assert pos.entry_price == pytest.approx(100.0)
    assert pos.stop_price == pytest.approx(98.0)   # 2% fallback (no numeric invalidation, no ATR)
    assert pos.notional == pytest.approx(500.0)    # 10 risk budget / 0.02 stop distance


def test_position_stopped_out_checks_side_correctly():
    long = LivePaperPosition("BTC/USDT", "1D", "LONG", "CONVERGENT", 100, 100.0, 1.0, stop_price=95.0)
    assert long.stopped_out(bar_low=94.0, bar_high=None) is True   # long low breaches
    assert long.stopped_out(bar_low=97.0, bar_high=None) is False
    short = LivePaperPosition("BTC/USDT", "1D", "SHORT", "CONVERGENT", 100, 100.0, 1.0, stop_price=106.0)
    assert short.stopped_out(bar_low=None, bar_high=108.0) is True  # short high breaches
    assert short.stopped_out(bar_low=None, bar_high=103.0) is False


def test_stopout_closes_at_stop_price_on_first_breach(tmp_path, monkeypatch):
    import pandas as pd

    from veyra.paper.live import LivePaperEngine

    class _StopEngine(LivePaperEngine):
        def _market_alerts(self, symbol, timeframe):
            # 40 obs (n>=20 floor) with a real edge so the quantified
            # decision gate passes and the stop logic itself is exercised.
            return [{"band": "CONVERGENT", "net_24h": x}
                    for x in (0.06, 0.04, 0.05, -0.01) * 10]

    # Candles: entry at ts0; the very next bar lows out at 90 (long stop 95).
    rows = []
    start = 1_700_000_000
    for i, (o, h, l, c) in enumerate([
        (100.0, 101, 99, 100),  # detection bar (i=0)
        (100.0, 108, 90, 106),  # next bar: low 90 breaches a 95 stop
        (106.0, 112, 104, 110),
        (110.0, 116, 108, 114),
    ]):
        rows.append({"open_time": start + i * 14400, "open": o, "high": h,
                     "low": l, "close": c, "volume": 1.0})
    df = pd.DataFrame(rows)

    class FakeStore:
        def load(self, s, t):
            return df

    class FakeScan:
        _store = FakeStore()

    eng = _StopEngine(ledger_dir=tmp_path, scan=FakeScan())
    eng._scan = FakeScan()

    # Short "now" so the horizon hasn't elapsed; only the stop should fire.
    monkeypatch.setattr("veyra.paper.live.time.time", lambda: start + 2 * 3600)
    setup = {
        "setup_type": "TREND_CONTINUATION", "side": "LONG", "regime": "BULL",
        "overall_score": 90, "timestamp": start, "time": "4H",
        "interest_area": {"low": 100.0, "high": 110.0}, "invalidation": 95.0,
        "reasoning": "t",
    }
    ledger = eng.run_checkpoint("BTC/USDT", "4H", amount=1000.0, refresh=False,
                                live={"setups": [setup], "live_price": 106.0})
    closed = [t for t in ledger.trades]
    assert len(closed) == 1
    assert closed[0].exit_reason == "stop"
    assert closed[0].stop_price == pytest.approx(95.0)
    assert closed[0].exit_price == pytest.approx(95.0)
    # LONG entered 100, stopped at 95 -> gross -5%, net ~ -5.3%.
    assert closed[0].gross_return == pytest.approx(-0.05)
    assert closed[0].net_return == pytest.approx(-0.053)


def test_aggregate_propagates_win_rate_and_pnl():
    from veyra.paper.live import LivePaperEngine

    results = [
        {"symbol": "BTC/USDT", "timeframe": "4H", "trades": 2, "wins": 1,
         "sum_pnl": 44.0, "sum_projected": 30.0, "open": [{"x": 1}], "win_rate": 0.5},
        {"symbol": "ETH/USDT", "timeframe": "1D", "trades": 0, "wins": 0,
         "sum_pnl": 0.0, "sum_projected": 0.0, "open": [], "win_rate": None},
    ]
    agg = LivePaperEngine._aggregate(results)
    assert agg["simulation"] is True
    assert agg["trades"] == 2
    assert agg["wins"] == 1
    assert agg["win_rate"] == pytest.approx(0.5)
    assert agg["sum_pnl"] == pytest.approx(44.0)
    assert agg["open_positions"] == 1
    assert "simulation only" in agg["note"]


def test_aggregate_survives_all_empty():
    from veyra.paper.live import LivePaperEngine

    agg = LivePaperEngine._aggregate([])
    assert agg["trades"] == 0
    assert agg["wins"] == 0
    assert agg["win_rate"] is None
    assert agg["sum_pnl"] == pytest.approx(0.0)


def test_run_all_visits_every_dataset(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from veyra.paper.live import LivePaperEngine

    datasets = [
        {"symbol": "BTC/USDT", "timeframe": "1D"},
        {"symbol": "ETH/USDT", "timeframe": "4H"},
    ]
    seen = []
    eng = LivePaperEngine.__new__(LivePaperEngine)

    def fake_checkpoint(symbol, timeframe, amount=1000.0, refresh=True, **kwargs):
        seen.append((symbol, timeframe))
        return SimpleNamespace(check_ct=1, last_check_ts=0)

    def fake_report(symbol, timeframe):
        return {"symbol": symbol, "timeframe": timeframe, "trades": 1, "wins": 1,
                "win_rate": 1.0, "sum_pnl": 12.0, "sum_projected": 10.0,
                "open": [], "closed": [], "cash": 10000.0, "last_check_ts": 0}

    eng.run_checkpoint = fake_checkpoint  # type: ignore[method-assign]
    eng.report = fake_report  # type: ignore[method-assign]
    agg = LivePaperEngine.run_all(eng, datasets=datasets, refresh=False)
    assert seen == [("BTC/USDT", "1D"), ("ETH/USDT", "4H")]
    assert agg["markets"][0]["symbol"] == "BTC/USDT"
    assert agg["win_rate"] == pytest.approx(1.0)


def test_horizon_is_short_for_15m_and_24h_for_classic():
    from veyra.alerts.forward import _field, horizon_for
    from veyra.paper.live import _horizon_seconds

    assert horizon_for("15m") == 2 * 3600
    assert horizon_for("4H") == 24 * 3600
    assert horizon_for("1D") == 24 * 3600
    # live.py delegates to the same source of truth.
    assert _horizon_seconds("15m") == horizon_for("15m")
    assert _horizon_seconds("1D") == horizon_for("1D")
    # Horizon-qualified field names: short datasets get 2h fields, 4H/1D stay 24h.
    assert _field("net", horizon_for("15m")) == "net_2h"
    assert _field("realized", horizon_for("15m")) == "realized_2h"
    assert _field("net", horizon_for("4H")) == "net_24h"
    assert _field("realized", horizon_for("4H")) == "realized_24h"