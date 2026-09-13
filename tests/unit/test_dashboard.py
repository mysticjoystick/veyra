"""Tests for the dashboard app and the alert service cache."""

from __future__ import annotations

import json

import pytest

from fastapi.testclient import TestClient

from veyra.alerts import AlertService, SetupAlert
from veyra.web.dashboard import create_dashboard


def _sample_alert() -> dict:
    return SetupAlert(
        alert_id="4H|a",
        symbol="BTC/USDT",
        timeframe="4H",
        setup_key="a",
        setup_type="TREND_CONTINUATION",
        side="LONG",
        regime="BULL",
        timestamp=1_650_000_000,
        overall_score=90,
        score_normalized=90,
        level="CONVERGENT",
        interest_area_low=50_000.0,
        interest_area_high=51_000.0,
        invalidation="close_below 49_000",
        reasoning="TREND_CONTINUATION LONG BULL score=90",
    ).to_dict()


class StubAuthService:
    """Accepts any session as an authenticated admin (dashboard tests only)."""

    def __init__(self):
        self.session_ttl_seconds = 3600

    def resolve_session(self, token):
        class _U:
            email = "admin@test.local"
            role = "admin"

        return _U()

    def authenticate(self, email, password):
        return "ok-token"

    def logout(self, token):
        return True

    def ensure_admin(self, email, password):
        return False


class StubAlertService:
    _datasets = [{"symbol": "BTC/USDT", "timeframe": "4H"}]

    def datasets(self):
        return list(self._datasets)

    def latest(self, n=10):
        a = dict(_sample_alert())
        a["symbol"] = "BTC/USDT"
        a["timeframe"] = "4H"
        a["outcome"] = "COMPLETED"
        a["won"] = True
        a["trade"] = {"exit_reason": "TARGET", "net_return": 0.04}
        return [a]

    def compute_all(self, force=False, blocking=True):
        a = _sample_alert()
        a["outcome"] = "COMPLETED"
        a["won"] = True
        a["trade"] = {"exit_reason": "TARGET", "net_return": 0.04}
        return {
            "datasets": [
                {
                    "symbol": "BTC/USDT",
                    "timeframe": "4H",
                    "capable": 10,
                    "qualified": 4,
                    "alert_count": 1,
                    "cached": False,
                    "cache_age_s": 0,
                    "summary": {"capable": 10, "qualified": 4, "alerts": 1},
                    "alerts": [a],
                }
            ],
            "last_error": None,
        }

    def portfolio(self, amount=None, scan=None, refresh=True, blocking=True):
        return {
            "amount": amount or 1000.0,
            "markets": [
                {
                    "symbol": "ETH/USDT",
                    "timeframe": "1D",
                    "rank": 1,
                    "live_price": 2480.10,
                    "regime": "BULL",
                    "band": "CONVERGENT",
                    "sample": 109,
                    "win_rate": 0.771,
                    "mean_24h": 0.0734,
                    "risk_24h": 0.02,
                    "projected_pnl": 73.4,
                    "risk_usd": 20.0,
                    "histogram": {"bins": 16, "lo": -0.25, "hi": 0.25, "counts": [0,0,0,0,0,1,1,2,3,4,3,2,1,0,0,0], "edges": []},
                    "verdict": "TRADE",
                    "verdict_label": "trade",
                },
                {
                    "symbol": "BTC/USDT",
                    "timeframe": "1D",
                    "rank": 2,
                    "live_price": 80268.00,
                    "regime": "BULL",
                    "band": "CONVERGENT",
                    "sample": 129,
                    "win_rate": 0.736,
                    "mean_24h": 0.0352,
                    "risk_24h": 0.015,
                    "projected_pnl": 35.2,
                    "risk_usd": 15.0,
                    "verdict": "TRADE",
                    "verdict_label": "trade",
                },
            ],
        }

    def walkforward(self, fraction=0.30, blocking=True):
        return {
            "method": "walkforward",
            "fraction": fraction,
            "n_markets": 1,
            "gate": "PASS",
            "gate_reason": "ALL markets hold their edge on unseen test data",
            "markets": [
                {
                    "symbol": "BTC/USDT",
                    "timeframe": "4H",
                    "n_alerts": 20,
                    "n_train": 14,
                    "n_test": 6,
                    "gate": "PASS",
                    "bands": [
                        {
                            "band": "CONVERGENT",
                            "gate": "PASS",
                            "note": "edge survives OOS: est. +3.20% 24h, 70% win (train 75%)",
                            "train": {"n": 10, "win_rate": 0.75, "mean_return": 0.035, "median_return": 0.03, "risk": 0.02},
                            "test": {"n": 6, "win_rate": 0.70, "mean_return": 0.032, "median_return": 0.03, "risk": 0.02},
                            "hold": True,
                        }
                    ],
                }
            ],
        }


class StubLiveScan:
    def live_all(self, datasets, refresh=True):
        return [
            {
                "symbol": "BTC/USDT",
                "timeframe": "4H",
                "regime": "BULL",
                "overall_score": 90,
                "latest_ts": 1_650_000_000,
                "analysed_bars": 400,
                "deep_enough": True,
                "freshness": "fresh",
                "live_price": 67500.0,
                "bar": {"interval_s": 14400, "next_open": 1_650_014_400, "seconds_left": 7200, "age_s": 14400},
                "setups": [
                    {
                        "setup_type": "TREND_CONTINUATION",
                        "side": "LONG",
                        "regime": "BULL",
                        "overall_score": 90,
                        "scores": {},
                        "timestamp": 1_650_000_000,
                        "interest_area": None,
                        "invalidation": "i",
                        "reasoning": "TREND_CONTINUATION LONG BULL",
                    }
                ],
            }
        ]

    def chart(self, symbol="BTC/USDT", timeframe="4H", limit=80, refresh=True):
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "fresh": "live",
            "candles": [
                {"time": 1_650_000_000, "o": 60000.0, "h": 61000.0, "l": 59500.0, "c": 60800.0, "v": 100.0},
                {"time": 1_650_014_400, "o": 60800.0, "h": 62500.0, "l": 60500.0, "c": 62300.0, "v": 120.0},
            ],
            "live_price": 67500.0,
            "bar": {"interval_s": 14400, "next_open": 1_650_028_800, "seconds_left": 3600, "age_s": 3600},
        }


def _make_client():
    return TestClient(
        create_dashboard(service=StubAlertService(), live_scan=StubLiveScan(), auth_service=StubAuthService())
    )


def test_dashboard_renders_html_and_shows_alert():
    client = _make_client()
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "TREND_CONTINUATION" in resp.text
    assert "SIGNALS ONLY" in resp.text.upper() or "NEVER AN ORDER" in resp.text.upper()
    assert "CONVERGENT" in resp.text


def test_api_chart_returns_live_candles():
    client = TestClient(create_dashboard(service=StubAlertService(), live_scan=StubLiveScan(), auth_service=StubAuthService()))
    resp = client.get("/api/chart?symbol=BTC/USDT&timeframe=4H&limit=80")
    assert resp.status_code == 200
    body = resp.json()
    assert body["fresh"] == "live"
    assert body["live_price"] == 67500.0
    assert len(body["candles"]) == 2
    assert body["bar"]["seconds_left"] == 3600


def test_root_renders_realtime_chart_ui():
    client = _make_client()
    resp = client.get("/")
    assert "Realtime chart" in resp.text
    assert "chartSvg" in resp.text
    assert "live tick" in resp.text


def test_api_alerts_returns_json():
    client = TestClient(create_dashboard(service=StubAlertService(), live_scan=StubLiveScan(), auth_service=StubAuthService()))
    resp = client.get("/api/alerts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["datasets"][0]["alert_count"] == 1
    assert body["datasets"][0]["alerts"][0]["level"] == "CONVERGENT"


def test_api_status_reports_datasets():
    client = TestClient(create_dashboard(service=StubAlertService(), live_scan=StubLiveScan(), auth_service=StubAuthService()))
    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "veyra"
    assert body["datasets"] == [{"symbol": "BTC/USDT", "timeframe": "4H"}]


def test_dashboard_handles_pending_datasets():
    class PendingService:
        _datasets = [{"symbol": "BTC/USDT", "timeframe": "4H"}]

        def datasets(self):
            return list(self._datasets)

        def latest(self, n=10):
            return []

        def portfolio(self, amount=None, scan=None, refresh=True, blocking=True):
            return {"amount": amount or 1000.0, "markets": []}

        def walkforward(self, fraction=0.30, blocking=True):
            return {"fraction": fraction, "status": "pending"}

        def compute_all(self, force=False, blocking=True):
            return {
                "datasets": [
                    {
                        "symbol": "BTC/USDT",
                        "timeframe": "4H",
                        "pending": True,
                        "capable": None,
                        "qualified": None,
                        "alert_count": None,
                        "alerts": [],
                        "message": "not computed yet; run `veyra alert` to pre-warm the cache",
                    }
                ],
                "last_error": None,
            }

    client = TestClient(
        create_dashboard(service=PendingService(), live_scan=StubLiveScan(), auth_service=StubAuthService())
    )
    resp = client.get("/")
    assert resp.status_code == 200
    assert "not computed yet" in resp.text


def test_alert_service_cache_reuses_file(tmp_path):
    calls = {"n": 0}

    class MonkeyAlertService(AlertService):
        def _run(self, symbol, timeframe):
            calls["n"] += 1
            from veyra.backtest import SimulationResult

            return SimulationResult(run=None, setups=[])

    svc = MonkeyAlertService(cache_dir=tmp_path, datasets=[{"symbol": "BTC/USDT", "timeframe": "1D"}])
    first = svc.compute_one("BTC/USDT", "1D")
    assert calls["n"] == 1
    assert first["cached"] is False

    cached = svc.compute_one("BTC/USDT", "1D")
    assert calls["n"] == 1, "second compute must reuse the cache, not re-run"
    assert cached["cached"] is True

    svc.compute_one("BTC/USDT", "1D", force=True)
    assert calls["n"] == 2, "force=True must bypass the cache"


def test_alert_service_nonblocking_marks_pending(tmp_path):
    class MonkeyAlertService(AlertService):
        def _run(self, symbol, timeframe):
            from veyra.backtest import SimulationResult

            return SimulationResult(run=None, setups=[])

    svc = MonkeyAlertService(cache_dir=tmp_path, datasets=[{"symbol": "BTC/USDT", "timeframe": "1D"}])
    payload = svc.compute_all(blocking=False)
    ds = payload["datasets"][0]
    assert ds["pending"] is True
    assert ds["alerts"] == []
    assert "veyra alert" in ds["message"]


def test_alert_service_bundle_shape(tmp_path):
    class MonkeyAlertService(AlertService):
        def _run(self, symbol, timeframe):
            from veyra.backtest import SimulationResult

            return SimulationResult(run=None, setups=[])

    svc = MonkeyAlertService(cache_dir=tmp_path, datasets=[{"symbol": "BTC/USDT", "timeframe": "1D"}])
    bundle = svc.compute_one("BTC/USDT", "1D")
    for key in ("symbol", "timeframe", "capable", "qualified", "alert_count", "alerts"):
        assert key in bundle, key
    # cache file exists and is valid JSON
    cached = tmp_path / "alerts-BTC-USDT-1D.json"
    assert cached.exists()
    json.loads(cached.read_text(encoding="utf-8"))


def test_api_latest_returns_recent_alerts():
    client = TestClient(create_dashboard(service=StubAlertService(), live_scan=StubLiveScan(), auth_service=StubAuthService()))
    resp = client.get("/api/latest?n=3")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["latest"]) == 1
    assert body["latest"][0]["symbol"] == "BTC/USDT"
    assert body["latest"][0]["outcome"] == "COMPLETED"


def test_dashboard_renders_outcome_badge():
    client = TestClient(create_dashboard(service=StubAlertService(), live_scan=StubLiveScan(), auth_service=StubAuthService()))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "+4.0%" in resp.text  # win badge from trade net_return 0.04
    assert "TARGET" in resp.text


def test_service_latest_flattens_and_sorts(tmp_path):
    # Seed two cached bundles with alerts at different timestamps, then confirm
    # `latest()` flattens across datasets, tags each market, and sorts newest-first.
    bundle_1d = {
        "symbol": "BTC/USDT",
        "timeframe": "1D",
        "alerts": [dict(_sample_alert(), timestamp=100, setup_key="old")],
    }
    bundle_4h = {
        "symbol": "BTC/USDT",
        "timeframe": "4H",
        "alerts": [dict(_sample_alert(), timestamp=300, setup_key="new")],
    }
    svc = AlertService(
        cache_dir=tmp_path,
        datasets=[
            {"symbol": "BTC/USDT", "timeframe": "1D"},
            {"symbol": "BTC/USDT", "timeframe": "4H"},
        ],
    )
    (tmp_path / "alerts-BTC-USDT-1D.json").write_text(json.dumps(bundle_1d), encoding="utf-8")
    (tmp_path / "alerts-BTC-USDT-4H.json").write_text(json.dumps(bundle_4h), encoding="utf-8")

    latest = svc.latest(10)
    assert [a["setup_key"] for a in latest] == ["new", "old"]
    assert latest[0]["symbol"] == "BTC/USDT"
    assert latest[1]["timeframe"] == "1D"


def test_service_latest_bounded_by_n(tmp_path):
    bundle_1d = {
        "symbol": "BTC/USDT",
        "timeframe": "1D",
        "alerts": [dict(_sample_alert(), timestamp=100 + i, setup_key=f"k{i}") for i in range(5)],
    }
    svc = AlertService(cache_dir=tmp_path, datasets=[{"symbol": "BTC/USDT", "timeframe": "1D"}])
    (tmp_path / "alerts-BTC-USDT-1D.json").write_text(json.dumps(bundle_1d), encoding="utf-8")
    assert len(svc.latest(3)) == 3


def test_service_enriches_alert_with_trade_result(tmp_path):
    from veyra.backtest import BacktestTrade, SetupRecord, SimulationResult

    class MonkeyAlertService(AlertService):
        def _run(self, symbol, timeframe):
            trade = BacktestTrade(
                trade_id="t1", setup_key="a", symbol="BTC/USDT", timeframe="1D",
                setup_type="TREND_CONTINUATION", regime="BULL", score=90,
                score_normalized=90, side="LONG", detection_ts=100,
                qualification_ts=100, entry_ts=100, entry_price=50000.0,
                invalidation="i", exit_ts=200, exit_price=52000.0,
                exit_reason="TARGET", gross_return=0.04, fees=0.0, slippage=0.0,
                net_return=0.04, holding_bars=1,
                max_favorable_excursion=0.0, max_adverse_excursion=0.0,
            )
            setup = SetupRecord(
                key="a", symbol="BTC/USDT", timeframe="1D", detection_ts=100,
                setup_type="TREND_CONTINUATION", regime="BULL", score=90,
                score_normalized=90, side="LONG", interest_area_low=49500.0,
                interest_area_high=50500.0, invalidation="i", final_state="COMPLETED",
                outcome="COMPLETED", trade_id="t1",
            )
            return SimulationResult(run=None, setups=[setup], trades=[trade])

    svc = MonkeyAlertService(cache_dir=tmp_path, datasets=[{"symbol": "BTC/USDT", "timeframe": "1D"}])
    bundle = svc.compute_one("BTC/USDT", "1D")
    assert bundle["alert_count"] == 1
    alert = bundle["alerts"][0]
    assert alert["outcome"] == "COMPLETED"
    assert alert["won"] is True
    assert alert["trade"]["exit_reason"] == "TARGET"
    assert abs(alert["trade"]["net_return"] - 0.04) < 1e-9


def test_service_reprices_returns_net_of_costs(tmp_path):
    """Step 2: every alert gets net_24h, and stats/portfolio use cost-adjusted values."""
    from veyra.alerts.costs import CostModel
    from veyra.backtest import BacktestTrade, SetupRecord, SimulationResult

    class _CostAlertService(AlertService):
        def _run(self, symbol, timeframe):
            trade = BacktestTrade(
                trade_id="t1", setup_key="a", symbol="BTC/USDT", timeframe="1D",
                setup_type="TREND_CONTINUATION", regime="BULL", score=90,
                score_normalized=90, side="LONG", detection_ts=100,
                qualification_ts=100, entry_ts=100, entry_price=50000.0,
                invalidation="i", exit_ts=200, exit_price=55000.0,
                exit_reason="TARGET", gross_return=0.10, fees=0.0, slippage=0.0,
                net_return=0.10, holding_bars=1,
                max_favorable_excursion=0.0, max_adverse_excursion=0.0,
            )
            setup = SetupRecord(
                key="a", symbol="BTC/USDT", timeframe="1D", detection_ts=100,
                setup_type="TREND_CONTINUATION", regime="BULL", score=90,
                score_normalized=90, side="LONG", interest_area_low=49500.0,
                interest_area_high=50500.0, invalidation="i", final_state="COMPLETED",
                outcome="COMPLETED", trade_id="t1",
            )
            return SimulationResult(run=None, setups=[setup], trades=[trade])

    cost = CostModel(fee_per_side=0.001, slippage_per_side=0.0005)  # 0.30% round-trip
    svc = _CostAlertService(cache_dir=tmp_path, datasets=[{"symbol": "BTC/USDT", "timeframe": "1D"}], cost_model=cost)
    bundle = svc.compute_one("BTC/USDT", "1D")
    alert = bundle["alerts"][0]
    assert "net_24h" in alert
    assert alert["net_24h"] == pytest.approx(alert["realized_24h"] - cost.round_trip)

    fwd = bundle["summary"]["forward_stats"]
    assert fwd["net_cost"] == pytest.approx(0.003)
    assert "gross" in fwd and "net" in fwd
    assert fwd["net"]["CONVERGENT"]["mean_return"] == pytest.approx(
        fwd["gross"]["CONVERGENT"]["mean_return"] - 0.003
    )

    # Portfolio ranks on net values and exposes the cost assumption.
    pf = svc.portfolio(amount=1000)
    row = next(r for r in pf["markets"] if r["band"] == "CONVERGENT")
    assert row["version"] == "net"
    assert row["cost_round_trip"] == pytest.approx(0.003)
    assert row["projected_pnl"] == pytest.approx(
        fwd["net"]["CONVERGENT"]["mean_return"] * 1000, rel=1e-6
    )


def test_api_live_returns_current_setups():
    client = _make_client()
    resp = client.get("/api/live")
    assert resp.status_code == 200
    body = resp.json()
    market = body["markets"][0]
    assert market["symbol"] == "BTC/USDT"
    assert market["setups"][0]["setup_type"] == "TREND_CONTINUATION"
    assert market["analysed_bars"] == 400


def test_dashboard_renders_live_predictions_section():
    client = _make_client()
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Live predictions" in resp.text
    assert "TREND_CONTINUATION" in resp.text


def test_dashboard_renders_prediction_card_with_live_info():
    client = _make_client()
    resp = client.get("/")
    assert resp.status_code == 200
    # The overall 90 stub should be rendered at the top CONVERGENT clarity band.
    assert "CONVERGENT" in resp.text
    assert "LIVE" in resp.text.upper()
    # The prediction card is rendered client-side from the lifecycle payload:
    # it must carry the live price, bar countdown, forming preview and projection.
    j = client.get("/api/lifecycle").json()
    m = next(x for x in j["markets"] if x["timeframe"] == "4H")
    assert m["live_price"] == 67500.0
    assert m["context"]["bar"]["seconds_left"] == 7200
    assert "preview" in m
    assert "projection" in m


def test_bar_lifecycle_reports_candle_age_and_countdown():
    from veyra.alerts.live import LiveScan

    bar = LiveScan._bar_lifecycle(1_000_000_000, "4H")
    assert bar["interval_s"] == 14400
    assert bar["next_open"] == 1_000_014_400
    assert bar["seconds_left"] >= 0
    assert bar["age_s"] >= 0


def test_clarity_helper_maps_score_to_band():
    from veyra.web.dashboard import _clarity_for

    assert _clarity_for(85) == "CONVERGENT"
    assert _clarity_for(58) == "DIRECTIONAL"
    assert _clarity_for(42) == "EMERGENT"
    assert _clarity_for(10) == "SCANNED"
    assert _clarity_for(None) == "SCANNED"


def test_livescan_returns_zero_setups_on_no_data():
    from veyra.alerts.live import LiveScan

    class EmptyStore:
        def load(self, symbol, timeframe):
            return None

    scan = LiveScan(lookback=50)
    scan._store = EmptyStore()
    out = scan.live("BTC/USDT", "1D", refresh=False)
    assert out["error"] == "no candles"


def test_livescan_calls_refresh_before_scanning():
    from veyra.alerts.live import LiveScan
    from veyra.data.service import IngestResult

    calls = []

    class FakeRefresh:
        def ingest(self, symbol, timeframe, incremental=True):
            calls.append((symbol, timeframe, incremental))
            return IngestResult(
                symbol=symbol, timeframe=timeframe, provider="fake",
                new_count=1, fetched_count=2,
            )

    class EmptyStore:
        def load(self, symbol, timeframe):
            return None

    scan = LiveScan(lookback=50, refresh_service=FakeRefresh())
    scan._store = EmptyStore()
    out = scan.live("BTC/USDT", "1D", refresh=True)
    assert calls == [("BTC/USDT", "1D", True)]
    assert out["error"] == "no candles"


def test_livescan_survives_refresh_failure():
    from veyra.alerts.live import LiveScan

    class BrokenRefresh:
        def ingest(self, symbol, timeframe, incremental=True):
            raise RuntimeError("network down")

    class EmptyStore:
        def load(self, symbol, timeframe):
            return None

    scan = LiveScan(lookback=50, refresh_service=BrokenRefresh())
    scan._store = EmptyStore()
    # Must not raise; falls back to stored data scan.
    out = scan.live("BTC/USDT", "1D", refresh=True)
    assert out["error"] == "no candles"


def test_chart_returns_live_candles_from_provider():
    import pandas as pd

    from veyra.alerts.live import LiveScan

    class Delegate:
        kind = "fake"

        class _C:
            def __init__(self, t, o, h, l, c, v):
                self.open_time = t
                self.open = o
                self.high = h
                self.low = l
                self.close = c
                self.volume = v

        def get_ohlcv(self, symbol, timeframe):
            return [
                self._C(1_650_000_000, 100, 110, 95, 108, 1),
                self._C(1_650_014_400, 108, 115, 106, 113, 2),
            ]

        def get_realtime_price(self, symbol):
            return 113.5

    class EmptyStore:
        def load(self, symbol, timeframe):
            return None

    scan = LiveScan(lookback=50)
    scan._provider_cache = Delegate()
    scan._store = EmptyStore()
    out = scan.chart("BTC/USDT", "4H", limit=80, refresh=True)
    assert out["fresh"] == "live"
    assert out["live_price"] == 113.5
    assert len(out["candles"]) == 2
    assert out["candles"][-1]["c"] == 113
    assert out["bar"]["interval_s"] == 14400  # 4H


def test_chart_falls_back_to_stored_when_provider_offline():
    from veyra.alerts.live import LiveScan

    class Offline:

        def get_ohlcv(self, symbol, timeframe):
            raise RuntimeError("network down")

        def get_realtime_price(self, symbol):
            return None

    class FilledStore:
        def load(self, symbol, timeframe):
            import pandas as pd
            return pd.DataFrame(
                [
                    {"open_time": 1_650_000_000, "open": 100, "high": 110, "low": 95, "close": 108, "volume": 1},
                    {"open_time": 1_650_014_400, "open": 108, "high": 115, "low": 106, "close": 113, "volume": 2},
                ]
            )

    scan = LiveScan(lookback=50)
    scan._provider_cache = Offline()
    scan._store = FilledStore()
    out = scan.chart("BTC/USDT", "4H", limit=80, refresh=True)
    assert out["fresh"] == "stored"
    assert len(out["candles"]) == 2
    assert out["candles"][-1]["c"] == 113


def test_chart_returns_error_dict_when_no_data_anywhere():
    from veyra.alerts.live import LiveScan

    class Offline:
        def get_ohlcv(self, symbol, timeframe):
            raise RuntimeError("down")

        def get_realtime_price(self, symbol):
            return None

    class EmptyStore:
        def load(self, symbol, timeframe):
            return None

    scan = LiveScan(lookback=50)
    scan._provider_cache = Offline()
    scan._store = EmptyStore()
    out = scan.chart("BTC/USDT", "4H", limit=80, refresh=True)
    assert "error" in out