"""Fast "live" market scan with realtime refresh.

The history-aware AlertService replays the whole dataset to enumerate every
setup that ever formed — that is correct for an audit but far too slow to run
on every dashboard load. For a realtime view we do NOT replay history.

LiveScan does two cheap things on demand:

  1. REFRESH: incrementally pull only the newest candles since the last stored
     one via the data pipeline (offline-safe — any network/provider failure
     simply falls back to the already-stored tail).
  2. SCAN: take the newest LOOKBACK bars from the store, run the market
     pipeline over that single window, and ask the setup engine what is forming
     on the latest bar.

Both are O(lookback), not O(history), so the live view is millis-fast and safe
to call on every page load.

Simulation only: this analyses candles and surfaces opportunities. It presents
nothing, places nothing, and never executes.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

from ..config import Settings, get_settings
from ..data.candle_store import CandleStore
from ..data.provider import get_provider
from ..data.service import MarketDataService
from ..database.dataset_repository import DatasetRepository
from ..domain.setup import Setup as DomainSetup
from ..market.pipeline import MarketAnalysisPipeline
from ..strategy.setup_engine import SetupEngine

logger = logging.getLogger(__name__)

# Bars of tail history needed for sound current-window analysis. The trend
# engine's warmup dominates (~ema_slow + slope), and we leave margin for
# structure pivots and momentum windows.
LIVE_LOOKBACK = 400

# Any number of extra bars to fetch on refresh to guarantee a coherent tail
# even if the stored data is slightly stale.
_REFRESH_EXTRA_BARS = 3

# Short result cache: the dashboard polls /api/live, /api/efficiency and the
# runner from the same shared LiveScan within a few seconds of each other.
# Caching avoids re-running the ingest + pipeline on every poll. TTL is kept
# above the dashboard's 15s poll so each front-end poll window is served from
# cache; the 60s runner always sees fresh data.
_CACHE_TTL_S = 20.0


class LiveScan:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        lookback: int = LIVE_LOOKBACK,
        refresh_service: Optional[MarketDataService] = None,
        provider_name: str = "binance",
        telemetry=None,
    ) -> None:
        self._settings = settings or get_settings()
        self._lookback = max(lookback, 1)
        self._pipeline = MarketAnalysisPipeline.default(self._settings)
        self._setup_engine = SetupEngine(self._settings)
        self._store = CandleStore(self._settings)
        self._refresh = refresh_service
        self._provider_name = provider_name
        self._last_refresh: Dict[str, object] = {}
        self._provider_cache = None
        self._service_cache = None
        self._cache: Dict[str, tuple] = {}
        self._telemetry = telemetry
        if self._telemetry is None:
            try:
                from ..market.regime_telemetry import RegimeTelemetry

                self._telemetry = RegimeTelemetry(
                    Path(self._settings.absolute_data_dir) / "regime_transitions.jsonl"
                )
            except Exception:  # noqa: BLE001 - telemetry must never break scans
                self._telemetry = None

    def _clear_cache(self) -> None:
        self._cache.clear()

    def live(self, symbol: str, timeframe: str, refresh: bool = True) -> dict:
        """Refresh (optional) then analyse the latest candle tail.

        Returns a serializable snapshot-of-now:
          * freshness: how old the newest candle is (seconds) + whether we
            refreshed or served stored data (e.g. offline)
          * the tail window size actually analysed + the newest bar timestamp
          * a list of setups detected on that newest bar (with genuine scores)
          * whether the tail was large enough for reliable component scoring

        `refresh` never blocks indefinitely and never fails the scan: a network
        error just results in `refresh="stored"` plus a `refresh_error` note.
        """
        import time as _time

        key = f"{symbol}|{timeframe}"
        now = _time.time()
        cached = self._cache.get(key)
        if cached is not None and now - cached[0] < _CACHE_TTL_S:
            return cached[1]

        if refresh:
            self._maybe_refresh(symbol, timeframe, refresh_out=self)

        df = self._store.load(symbol, timeframe)
        if df is None or df.empty:
            return {"symbol": symbol, "timeframe": timeframe, "error": "no candles"}
        df = df.sort_values("open_time").reset_index(drop=True)
        tail = df.tail(self._lookback).reset_index(drop=True)
        if tail.empty:
            return {"symbol": symbol, "timeframe": timeframe, "error": "empty tail"}

        snapshot = self._pipeline.analyze(symbol, timeframe, tail)
        setups = self._setup_engine.detect(snapshot)

        if self._telemetry is not None:
            try:
                self._telemetry.observe(
                    symbol,
                    timeframe,
                    snapshot.regime.value,
                    snapshot.regime_output.evidence,
                    ts=int(snapshot.timestamp),
                )
            except Exception as exc:  # noqa: BLE001 - telemetry never fails the scan
                self._telemetry = None
                logger.warning("regime telemetry disabled: %s", exc)

        fallback = {"state": "stored", "note": "not refreshed this pass"}
        refresh_state = self._last_refresh or fallback
        if refresh_state.get("state") == "ok":
            fresh = "fresh" if refresh_state.get("new", 0) > 0 else "fresh"
        else:
            fresh = "stored"

        price = self._fetch_live_price(symbol)
        bar = self._bar_lifecycle(snapshot.timestamp, timeframe)
        preview = self._forming_preview(snapshot)

        result = {
            "symbol": symbol,
            "timeframe": timeframe,
            "analysed_bars": int(len(tail)),
            "latest_ts": snapshot.timestamp,
            "regime": snapshot.regime.value,
            "regime_reason": self._regime_reason(snapshot.regime.value, snapshot.regime_output.evidence),
            "deep_enough": not snapshot.data_quality.is_insufficient,
            "overall_score": snapshot.overall_score,
            "freshness": fresh,
            "refresh": refresh_state,
            "live_price": price,
            "bar": bar,
            "preview": preview,
            "setups": [self._serialize(s) for s in setups],
        }
        self._cache[key] = (now, result)
        return result

    def live_all(self, datasets: List[Dict[str, str]], refresh: bool = True) -> List[dict]:
        from concurrent.futures import ThreadPoolExecutor

        jobs = {i: ds for i, ds in enumerate(datasets)}
        out: List[dict] = []
        with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as pool:
            futures = {
                pool.submit(self._scan_one, symbol, timeframe, refresh): idx
                for idx, (symbol, timeframe) in (
                    (idx, (ds["symbol"], ds["timeframe"])) for idx, ds in jobs.items()
                )
            }
            ordered = [
                (futures[f], f.result())
                for f in futures
            ]
        # Sort back to the original dataset order.
        ordered.sort(key=lambda pair: pair[0])
        return [res for _, res in ordered]

    def _scan_one(self, symbol: str, timeframe: str, refresh: bool) -> dict:
        try:
            return self.live(symbol, timeframe, refresh=refresh)
        except Exception as exc:  # noqa: BLE001 - never fail the whole page
            return {"symbol": symbol, "timeframe": timeframe, "error": str(exc)}

    # -- Realtime refresh -------------------------------------------------

    def chart(
        self, symbol: str, timeframe: str, limit: int = 80, refresh: bool = True
    ) -> dict:
        """Realtime OHLC series for a live candlestick chart.

        Prefers *live* candle data fetched from the provider (so the in-progress
        bar's high/low/close reflect the real market right now), falling back to
        the stored tail when offline. Returns:

          * candles : most-recent [o,h,l,c,volume,time] rows, newest last
          * live_price : latest trade price (extends the in-progress bar)
          * bar      : interval_s / next_open / seconds_left / age_s
          * fresh    : 'live' when fetched from the provider, else 'stored'

        Offline-safe: a network/provider failure never raises.
        """
        import time as _time

        key = f"chart|{symbol}|{timeframe}|{limit}"
        now = _time.time()
        cached = self._cache.get(key)
        if cached is not None and now - cached[0] < _CACHE_TTL_S:
            return cached[1]

        barrier = {"interval_s": 0, "next_open": 0, "seconds_left": 0, "age_s": 0}
        live_price = None
        fresh = "stored"

        try:
            if refresh:
                raw = self._provider().get_ohlcv(symbol, timeframe)
                if raw:
                    raw = raw[-max(1, limit):]
                    candles = [
                        {
                            "time": c.open_time,
                            "o": c.open,
                            "h": c.high,
                            "l": c.low,
                            "c": c.close,
                            "v": c.volume,
                        }
                        for c in raw
                    ]
                    fresh = "live"
        except Exception as exc:  # noqa: BLE001 - offline tolerance
            logger.warning("Live chart fetch failed for %s %s: %s", symbol, timeframe, exc)
            candles = None
        else:
            candles = candles or None

        if candles is None or not candles:
            df = self._store.load(symbol, timeframe)
            if df is not None and not df.empty:
                df = df.sort_values("open_time").reset_index(drop=True).tail(limit)
                row = df.to_dict("records")
                candles = [
                    {
                        "time": r["open_time"],
                        "o": r["open"],
                        "h": r["high"],
                        "l": r["low"],
                        "c": r["close"],
                        "v": r["volume"],
                    }
                    for r in row
                ]

        if not candles:
            return {"symbol": symbol, "timeframe": timeframe, "error": "no data"}

        newest = candles[-1]
        live_price = self._fetch_live_price(symbol) or newest["c"]
        barrier = self._bar_lifecycle(newest["time"], timeframe)

        result = {
            "symbol": symbol,
            "timeframe": timeframe,
            "fresh": fresh,
            "candles": candles,
            "live_price": live_price,
            "bar": barrier,
        }
        self._cache[key] = (now, result)
        return result

    def _maybe_refresh(self, symbol: str, timeframe: str, refresh_out: "LiveScan") -> None:
        service = refresh_out._service()
        if service is None:
            refresh_out._last_refresh = {"state": "error", "error": "no refresh service"}
            return
        try:
            result = service.ingest(symbol, timeframe, incremental=True)
            refresh_out._last_refresh = {
                "state": "ok",
                "new": result.new_count,
                "fetched": result.fetched_count,
                "min_age_s": int(result.range_end - result.range_start) if (result.range_start and result.range_end) else 0,
            }
        except Exception as exc:  # noqa: BLE001 - offline tolerance
            logger.warning("Live refresh failed for %s %s: %s", symbol, timeframe, exc)
            refresh_out._last_refresh = {"state": "error", "error": str(exc)}

    def _provider(self):
        if self._provider_cache is None:
            self._provider_cache = get_provider(self._provider_name)
        return self._provider_cache

    def _service(self) -> Optional[MarketDataService]:
        if self._refresh is not None:
            return self._refresh
        if self._service_cache is None:
            try:
                repo = DatasetRepository(self._session_factory())
                self._service_cache = MarketDataService(
                    self._provider(), self._store, repo, settings=self._settings
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Could not build live refresh service: %s", exc)
                self._service_cache = None
        return self._service_cache

    def _fetch_live_price(self, symbol: str) -> Optional[float]:
        """Best live price for a symbol, or None when offline/unavailable."""
        try:
            return self._provider().get_realtime_price(symbol)
        except Exception as exc:  # noqa: BLE001 - offline tolerance
            logger.warning("Live price fetch failed for %s: %s", symbol, exc)
            return None

    @staticmethod
    def _regime_reason(regime: str, evidence: dict) -> str:
        """Human-readable explanation of a market's regime, for the dashboard.

        Why is the scanner standing aside? Hoping for honest cards instead of a
        binary "no setup". Blank when there's no evidence so callers degrade.
        """
        if not evidence:
            return ""
        trend = evidence.get("trend")
        struct = evidence.get("structure")
        vol = evidence.get("volatility")
        if regime in ("BULL", "BEAR"):
            return f"{regime} · {trend} trend, {struct} structure, {vol} vol"
        if regime == "HIGH_VOLATILITY":
            return "HIGH_VOLATILITY · trending rules stand aside until vol normalizes"
        if regime == "RANGE":
            if trend == "NEUTRAL":
                return "RANGE · trend neutral (EMA cross unfinished) — range-fade scan active"
            return f"RANGE · {trend} trend but {struct} structure — range-fade scan active"
        if regime == "UNKNOWN":
            return "UNKNOWN regime · insufficient input"
        return ""

    @staticmethod
    def _bar_lifecycle(last_ts: int, timeframe: str) -> dict:
        """Describe the in-progress candle relative to the newest stored bar.

        * next_open     : when the current bar closes / the next bar opens (epoch s)
        * seconds_left  : seconds remaining until that close
        * age_s         : how long the current bar has been forming
        * interval_s    : bar length in seconds
        """
        import time

        from ..data.timeframe import timeframe_interval_seconds

        interval = timeframe_interval_seconds(timeframe)
        now = int(time.time())
        next_open = last_ts + interval
        return {
            "interval_s": interval,
            "next_open": next_open,
            "seconds_left": max(0, next_open - now),
            "age_s": max(0, now - last_ts),
        }

    def _session_factory(self):
        from ..database.engine import build_engine, make_session_factory

        engine = build_engine(self._settings)
        return make_session_factory(engine)

    @staticmethod
    def _serialize(s: DomainSetup) -> dict:
        from ..paper.quality import evaluate_eligibility

        eligibility = evaluate_eligibility(int(s.overall_score or 0))
        return {
            "setup_type": s.setup_type.value,
            "side": s.side.value,
            "regime": s.regime.value,
            "overall_score": s.overall_score,
            "scores": {k.value: v for k, v in s.scores.items()},
            "timestamp": s.timestamp,
            "interest_area": (
                {"low": s.interest_area.low, "high": s.interest_area.high}
                if s.interest_area
                else None
            ),
            "invalidation": s.invalidation,
            "reasoning": s.reasoning,
            "atr": s.evidence.get("atr"),
            "band": eligibility["band"],
            "trade_eligible": eligibility["trade_eligible"],
            "eligibility_reason": eligibility["eligibility_reason"],
        }

    def _forming_preview(self, snapshot: "MarketSnapshot") -> List[dict]:
        """Surface what is *closest* to a qualifying setup on the live bar.

        A preview is not a detection: it derives the same numbers the detectors
        read (retracement, distance-to-level, range-boundary gap) from the
        current intra-bar snapshot and reports the nearest one or two as an
        early heads-up for a human. Always provisional — the un-closed bar can
        still flip it, and the closed-bar scan remains the trigger.
        """
        from ..domain import MarketSide
        from ..strategy.detectors import rules
        from ..strategy.snapshot_view import SnapshotView

        v = SnapshotView(snapshot)
        price = v.price()
        if price is None or price <= 0:
            return []
        s = self._settings
        regime = snapshot.regime.value
        candidates: List[dict] = []

        def _climb(now: float, target: float) -> float:
            # Normalised closeness 0..1 to a threshold we must exceed.
            if target <= 0:
                return 0.0
            return max(0.0, min(1.0, now / target))

        def _trend():
            side = rules.side_of(regime)
            strength = rules.trend_strength(v)
            struct = rules.structure_state(v)
            mom = rules.momentum_state(v)
            hostile = (
                mom == ("NEGATIVE" if side == MarketSide.LONG else "POSITIVE")
                or struct in ("LH_LL", "LOWER_HIGHS_LOWER_LOWS") if side == MarketSide.LONG
                else struct in ("HH_HL", "HIGHER_HIGHS_HIGHER_LOWS")
            )
            min_strength = s.setup_min_trend_strength
            if strength < min_strength * 0.55:
                return None
            detail = f"trend {strength:.0f}/100 · {struct}"
            if hostile:
                detail += " · momentum/structure resisting"
                closeness = 0.4
            else:
                closest = _climb(strength, min_strength)
                detail += " · awaiting fresh HH/HL confirmation"
                closeness = closest
            return {
                "key": "TREND_CONTINUATION", "side": side.value,
                "label": "Continuation forming", "detail": detail,
                "closeness": closeness,
            }

        def _pullback():
            try:
                side = rules.side_of(regime)
            except ValueError:
                return None
            ref_hi = (
                rules.last_swing_high(v)
                if side == MarketSide.LONG else rules.last_swing_low(v)
            )
            ref_lo = (
                rules.last_swing_low(v)
                if side == MarketSide.LONG else rules.last_swing_high(v)
            )
            if not ref_hi or ref_hi <= 0:
                return None
            base = ref_lo or min(ref_hi, price)
            span = (ref_hi - base) if side == MarketSide.LONG else (base - ref_hi)
            if span <= 0:
                return None
            retrace = (
                (ref_hi - price) / span
                if side == MarketSide.LONG else (price - ref_hi) / span
            )
            lo_, hi_ = s.setup_pullback_min_retrace, s.setup_pullback_max_retrace
            strength = rules.trend_strength(v)
            if retrace > hi_:
                return None  # window already passed — not a forming pullback
            if retrace < lo_ * 0.5:
                return None  # far from the window — not worth previewing
            if strength < s.setup_min_trend_strength * 0.55:
                return None
            return {
                "key": "PULLBACK", "side": side.value, "label": "Pullback forming",
                "detail": f"retrace {retrace*100:.1f}% · needs ≥ {lo_*100:.1f}% (max {hi_*100:.0f}%)",
                "closeness": _climb(retrace, lo_),
            }

        def _breakout():
            try:
                side = rules.side_of(regime)
            except ValueError:
                side = None
            hi = rules.last_swing_high(v)
            lo = rules.last_swing_low(v)
            dist = s.setup_breakout_distance_pct
            entries = []
            if hi and price > hi:
                clear = (price - hi) / hi
                entries.append(("BREAKOUT", "LONG", f"price {price:.0f} > pivot {hi:.0f} · +{clear*100:.2f}%", _climb(clear, dist), "LONG"))
            if lo and price < lo:
                clear = (lo - price) / lo
                entries.append(("BREAKOUT", "SHORT", f"price {price:.0f} < pivot {lo:.0f} · −{clear*100:.2f}%", _climb(clear, dist), "SHORT"))
            if not entries and hi and lo:
                near_hi = (hi - price) / hi
                near_lo = (price - lo) / lo
                if min(near_hi, near_lo) < 0.01:
                    down = near_hi <= near_lo
                    lvl = hi if down else lo
                    side = "LONG" if down else "SHORT"
                    entries.append(("BREAKOUT", side, f"price {price:.0f} vs pivot {lvl:.0f} · {min(near_hi, near_lo)*100:.2f}% to break", _climb(0.005, dist) * 0.5, side))
            if entries:
                cl = entries[0]
                return {"key": cl[0], "side": cl[4], "label": "Breakout forming",
                        "detail": cl[2], "closeness": cl[3]}
            return None

        def _range():
            hi = rules.last_swing_high(v)
            lo = rules.last_swing_low(v)
            if not hi or not lo or hi <= lo:
                return None
            tol = s.setup_range_boundary_tolerance_pct
            up = (hi - price) / hi
            dn = (price - lo) / lo
            if min(up, dn) > tol:
                pos = (price - lo) / (hi - lo) * 100
                return {
                    "key": "RANGE_REJECTION", "side": "FLAT",
                    "label": "Range watch",
                    "detail": f"mid-range · {pos:.0f}% up the band ({lo:.0f}–{hi:.0f})",
                    "closeness": 0.3,
                }
            near_up = up <= dn
            bound = hi if near_up else lo
            gap = up if near_up else dn
            side = "SHORT" if near_up else "LONG"
            return {
                "key": "RANGE_REJECTION", "side": side,
                "label": "Range rejection forming",
                "detail": f"price {price:.0f} · {gap*100:.2f}% off {'upper' if near_up else 'lower'} bound {bound:.0f}",
                "closeness": 1.0 - (gap / tol if tol else 0),
            }

        if regime in ("BULL", "BEAR"):
            pres = [f for f in (_trend(), _pullback(), _breakout()) if f]
        elif regime == "RANGE":
            pres = [f for f in (_range(), _breakout()) if f]
        else:
            pres = []
        pres.sort(key=lambda p: p["closeness"], reverse=True)
        return [p for p in pres[:2] if p["closeness"] > 0]