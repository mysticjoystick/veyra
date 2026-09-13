"""Alert computation service.

Bridges the frozen backtest engine to the alert processor. `compute_all`
runs the frozen baseline over a market's candles, feeds the resulting
setups to the AlertProcessor, and returns a deterministic alert list plus a
capability summary. Results are cached to disk so the dashboard can load
fast on repeat visits; the cache is a pure memo (recompute when fresh).

Simulation-only: computing alerts runs no trade, touches no exchange, and
writes no execution state.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .forward import ForwardReturnModel, ForwardStats
from .models import SetupAlert
from .policy import AlertPolicy
from .processor import AlertProcessor
from ..backtest import SimulationResult
from ..config import Settings, get_settings
from ..data.candle_store import CandleStore
from ..phase5.frozen_config import (
    PHASE5_BASELINE_VERSION,
    build_baseline_engine,
)

DEFAULT_DATASETS = [
    {"symbol": "BTC/USDT", "timeframe": "1D"},
    {"symbol": "BTC/USDT", "timeframe": "4H"},
    {"symbol": "BTC/USDT", "timeframe": "1H"},
    {"symbol": "ETH/USDT", "timeframe": "1D"},
    {"symbol": "ETH/USDT", "timeframe": "4H"},
    {"symbol": "ETH/USDT", "timeframe": "1H"},
    {"symbol": "SOL/USDT", "timeframe": "1D"},
    {"symbol": "SOL/USDT", "timeframe": "4H"},
    {"symbol": "SOL/USDT", "timeframe": "1H"},
    {"symbol": "BNB/USDT", "timeframe": "1D"},
    {"symbol": "BNB/USDT", "timeframe": "4H"},
    {"symbol": "BNB/USDT", "timeframe": "1H"},
]

# Amount of time a cached alert snapshot may be served before recompute.
# The bundle is a full-history replay used for track-record aggregates; the
# realtime "live now" view comes from LiveScan, not this cache. A long TTL
# therefore keeps dashboards snappy without making the live view stale.
DEFAULT_CACHE_TTL_S = 24 * 3600


def _band_for(score: int) -> str:
    from .models import AlertLevel

    return AlertLevel.for_score(score).name


def _fmt(x) -> Optional[float]:
    return round(x, 6) if x is not None else None


def _histogram(alerts: List[dict], band: str, bins: int = 16, width: float = 0.25) -> dict:
    """Group realized 24h returns into fixed numeric buckets for a sparkline.

    Returns counts across `bins` buckets spanning [-width, +width] (centered
    on zero, which is the meaningful axis for HOLD-vs-TRADE). `edges` holds
    bucket lower edges as human-readable labels.
    """
    vals = [
        a.get("realized_24h")
        for a in alerts
        if a.get("band") == band and isinstance(a.get("realized_24h"), (int, float))
    ]
    counts = [0] * bins
    lo, hi = -width, width
    span = (hi - lo) / bins
    for v in vals:
        idx = int((v - lo) / span)
        idx = max(0, min(bins - 1, idx))
        counts[idx] += 1
    edges = [f"{lo + i * span:+.2f}" for i in range(bins)]
    return {"bins": bins, "lo": lo, "hi": hi, "counts": counts, "edges": edges}


class AlertService:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        policy: Optional[AlertPolicy] = None,
        cache_dir: Optional[Path] = None,
        cache_ttl_s: int = DEFAULT_CACHE_TTL_S,
        datasets: Optional[List[Dict[str, str]]] = None,
        cost_model: Optional["CostModel"] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._policy = policy or AlertPolicy()
        self._processor = AlertProcessor(self._policy)
        self._cache_dir = cache_dir or (self._settings.absolute_data_dir / "alerts")
        self._cache_ttl_s = cache_ttl_s
        self._datasets = datasets or DEFAULT_DATASETS
        from .costs import CostModel

        self._cost_model = cost_model or CostModel()

    # -- Public API --------------------------------------------------------

    def datasets(self) -> List[Dict[str, str]]:
        return list(self._datasets)

    def compute_all(self, force: bool = False, blocking: bool = True) -> List[dict]:
        """Compute (or load cached) alert bundles for every dataset.

        Each bundle is a serializable dict so the dashboard and CLI share one
        shape and the JSON cache round-trips cleanly.

        With `blocking=False` a dataset that has no cached bundle is reported as
        *pending* rather than computed inline. This keeps a page load fast and
        lets an operator pre-warm heavy datasets (e.g. 4H) up front via the CLI
        (`veyra alert`), after which the dashboard serves from cache instantly.
        """
        last_err: Optional[str] = None
        bundles: List[dict] = []
        for ds in self._datasets:
            sym, tf = ds["symbol"], ds["timeframe"]
            try:
                if not blocking and not force:
                    cached = self._load_compute_free(sym, tf)
                    if cached is None:
                        bundles.append(self._pending_bundle(sym, tf))
                        continue
                    bundles.append(cached)
                    continue
                bundles.append(self.compute_one(sym, tf, force))
                last_err = None
            except Exception as exc:  # noqa: BLE001 - report per-dataset, don't abort
                last_err = str(exc)
                bundles.append(
                    {
                        "symbol": sym,
                        "timeframe": tf,
                        "error": str(exc),
                        "alerts": [],
                        "summary": {},
                        "cached": False,
                    }
                )
        return {"datasets": bundles, "last_error": last_err}

    @staticmethod
    def _pending_bundle(symbol: str, timeframe: str) -> dict:
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "capable": None,
            "qualified": None,
            "alert_count": None,
            "cached": False,
            "pending": True,
            "summary": {},
            "alerts": [],
            "message": "not computed yet; run `veyra alert` to pre-warm the cache",
        }

    def compute_one(self, symbol: str, timeframe: str, force: bool = False) -> dict:
        """Compute or load a single dataset's alert bundle."""
        cache_path = self._cache_path(symbol, timeframe)
        if not force and cache_path.exists() and self._cache_fresh(cache_path):
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            data["alerts"] = sorted(
                data.get("alerts", []), key=lambda a: a.get("timestamp") or 0, reverse=True
            )
            data["cached"] = True
            data["cache_age_s"] = self._cache_age(cache_path)
            self._ensure_forward(data, symbol, timeframe)
            return data

        result = self._run(symbol, timeframe)
        alerts = self._processor.run(result)
        candles = CandleStore(self._settings).load(symbol, timeframe)
        bundle = self._to_bundle(symbol, timeframe, result, alerts, candles, cached=False)
        self._persist(cache_path, bundle)
        return bundle

    def latest(self, n: int = 10) -> List[dict]:
        """The n most recent enriched alerts across all cached datasets.

        This is the "live" view: the latest setups the strategy flagged, drawn
        from whatever is already in the cache (no heavy recompute). Alerts are
        flattened, tagged with their market, and sorted newest-first.
        """
        bundles = self.compute_all(blocking=False).get("datasets", [])
        flat: List[dict] = []
        for ds in bundles:
            # only cache-backed bundles are "ready"; skip pending/errored
            for a in ds.get("alerts", []):
                tagged = dict(a)
                tagged["symbol"] = ds["symbol"]
                tagged["timeframe"] = ds["timeframe"]
                flat.append(tagged)
        flat.sort(key=lambda a: a.get("timestamp", 0), reverse=True)
        return flat[: max(0, n)]

    # -- Internals ---------------------------------------------------------

    def _run_df(self, symbol: str, timeframe: str, df) -> SimulationResult:
        if df is None or df.empty:
            raise ValueError(f"No candles for {symbol} {timeframe}")
        df = df.sort_values("open_time").reset_index(drop=True)
        engine = build_baseline_engine(self._settings, progressive=True)
        return engine.run(
            symbol,
            timeframe,
            df.copy(),
            run_key=f"alerts|{symbol}|{timeframe}",
        )

    def _run(self, symbol: str, timeframe: str) -> SimulationResult:
        df = CandleStore(self._settings).load(symbol, timeframe)
        return self._run_df(symbol, timeframe, df)

    def _to_bundle(
        self,
        symbol: str,
        timeframe: str,
        result: SimulationResult,
        alerts: List[SetupAlert],
        candles,
        cached: bool,
    ) -> dict:
        qualified = sum(
            1
            for s in result.setups
            if s.outcome in ("QUALIFIED_NO_TRADE", "COMPLETED")
        )
        trades_by_setup = {t.setup_key: t for t in result.trades}
        enriched = [self._enrich(a, trades_by_setup) for a in alerts]
        self._apply_forward(enriched, symbol, timeframe, candles)
        enriched.sort(key=lambda a: a.get("timestamp") or 0, reverse=True)

        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "strategy_version": PHASE5_BASELINE_VERSION,
            "computed_at": int(time.time()),
            "cached": cached,
            "cache_age_s": 0,
            "capable": len(result.setups),
            "qualified": qualified,
            "alert_count": len(alerts),
            "summary": {
                "capable": len(result.setups),
                "qualified": qualified,
                "alerts": len(alerts),
                "by_level": self._by_level(alerts),
                "by_outcome": self._by_outcome(enriched),
                "forward_stats": self._forward_stats(enriched, symbol, timeframe, candles),
            },
            "alerts": enriched,
        }

    def _apply_forward(self, alerts: List[dict], symbol: str, timeframe: str, candles) -> None:
        """Attach realized + projected forward return and P&L to each alert dict.

        Two passes: first measure each alert's realized move over the dataset's
        horizon, then build the per-band history so each alert's projection has
        real sample sizes. The horizon (e.g. 24h, or short for 15m) is derived
        from the timeframe so projection and settle always agree.
        """
        from .forward import _field, _horizon_hours, horizon_for

        horizon = horizon_for(timeframe)
        model = ForwardReturnModel(candles, horizon=horizon)
        cost = self._cost_model
        r_field = _field("realized", horizon)
        n_field = _field("net", horizon)
        for d in alerts:
            d["band"] = _band_for(int(d.get("score_normalized") or 0))
            entry = d.get("interest_area_high") or d.get("interest_area_low")
            realized = _fmt(
                model.realized(
                    detection_ts=int(d.get("timestamp") or 0),
                    side=d.get("side") or "",
                    entry_price=(float(entry) if entry else None),
                    horizon=horizon,
                )
            )
            d[r_field] = realized
            d[n_field] = _fmt(cost.net_return(realized))
        stats = model.stats_by_band(alerts, field=r_field)
        for d in alerts:
            projection = model.project(d, stats, amount=d.get("pnl_amount")).to_dict()
            d["net_projected_return"] = _fmt(cost.net_return(projection.get("projected_return")))
            d["forward"] = projection
            d["horizon_hours"] = _horizon_hours(horizon)

    def _forward_stats(self, alerts: List[dict], symbol: str, timeframe: str, candles) -> dict:
        from .forward import _field, horizon_for

        horizon = horizon_for(timeframe)
        model = ForwardReturnModel(candles, horizon=horizon)
        r_field = _field("realized", horizon)
        n_field = _field("net", horizon)
        return {
            "net_cost": self._cost_model.round_trip,
            "horizon_hours": _field("net", horizon).rstrip("h").split("_")[-1],
            "gross": {
                band: st.to_dict()
                for band, st in model.stats_by_band(alerts, field=r_field).items()
            },
            "net": {
                band: st.to_dict()
                for band, st in model.stats_by_band(alerts, field=n_field).items()
            },
        }

    def _ensure_forward(self, bundle: dict, symbol: str, timeframe: str) -> None:
        """Enrich a cached bundle with forward returns without re-running the engine.

        Fast: reads candles, measures the horizon-after move for each alert, then
        updates the bundle in-place. Safe to call on every cache hit.
        """
        from .forward import _field, horizon_for

        candles = CandleStore(self._settings).load(symbol, timeframe)
        if candles is None or candles.empty:
            return
        alerts = bundle.get("alerts", [])
        need = _field("realized", horizon_for(timeframe))
        changed = False
        for d in alerts:
            if need not in d or "band" not in d or "forward" not in d:
                changed = True
                break
        if not changed and "forward_stats" in bundle.get("summary", {}):
            stats = bundle["summary"]["forward_stats"]
            if isinstance(stats, dict) and "net" in stats and "gross" in stats:
                net = stats["net"]
                if all(isinstance(v, dict) and "risk" in v for v in net.values()):
                    return
        self._apply_forward(alerts, symbol, timeframe, candles)
        summary = bundle.setdefault("summary", {})
        summary["forward_stats"] = self._forward_stats(alerts, symbol, timeframe, candles)
        self._persist(self._cache_path(symbol, timeframe), bundle)

    def project(self, symbol: str, timeframe: str, amount: Optional[float] = None) -> dict:
        """Projected forward P&L for a dataset's alerts based on historical bands.

        `amount` is the size you would trade; the returned `pnl_at_amount` is an
        estimate (amount * historical band mean), never a guarantee.
        """
        from .forward import horizon_for

        bundle = self.compute_one(symbol, timeframe)
        alerts = bundle.get("alerts", [])
        fwd = bundle.get("summary", {}).get("forward_stats", {})
        stats = {
            band: ForwardStats(**data)
            for band, data in (fwd.get("gross", {}) or {}).items()
        }
        model = ForwardReturnModel(
            CandleStore(self._settings).load(symbol, timeframe),
            horizon=horizon_for(timeframe),
        )
        cost = self._cost_model
        rows = []
        for a in alerts:
            proj = model.project(a, stats, amount=amount).to_dict()
            proj["net_projected_return"] = _fmt(cost.net_return(proj.get("projected_return")))
            rows.append(proj)
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "amount": amount,
            "count": len(rows),
            "costs": cost.to_dict(),
            "projections": rows,
        }

    def portfolio(
        self,
        amount: Optional[float] = None,
        scan: Optional[object] = None,
        refresh: bool = True,
        blocking: bool = True,
    ) -> dict:
        """Rank all markets by their projected 24h P&L for a clean decision stack.

        Returns, per market, a snapshot combining the live price with the market's
        CONVERGENT 24h history: win rate, mean move, mean risk, projected P&L at
        `amount`, and an honest TRADE/HOLD verdict. Sorted best-first.

        The verdict is historical guidance, never a promise:
          TRADE  : projected P&L > 0 AND win rate >= 55% AND the expected upside
                   outweighs the average downside (projected return > risk).
          WATCH  : still a positive expected move, but conditions to wait.
          HOLD   : no net positive edge from history.

        When `blocking=False`, datasets without a fresh cache are omitted (they
        get reported via the alert bundles / walkforward) so the response is fast.
        When `refresh=False`, the live scan is served from its short cache rather
        than hitting Binance again.
        """
        amt = float(amount) if amount else 1000.0
        from .live import LiveScan

        scan = scan or LiveScan()
        live = {
            f"{m['symbol']}|{m['timeframe']}": m
            for m in scan.live_all(self._datasets, refresh=refresh)
        }

        rows = []
        for ds in self._datasets:
            sym, tf = ds["symbol"], ds["timeframe"]
            try:
                bundle = self.compute_one(sym, tf) if blocking else self._load_if_cached(sym, tf)
            except Exception:  # noqa: BLE001 - one bad dataset must not kill the panel
                rows.append(
                    {
                        "symbol": sym,
                        "timeframe": tf,
                        "verdict_label": "no data",
                        "verdict": "HOLD",
                    }
                )
                continue
            if bundle is None:
                rows.append(
                    {
                        "symbol": sym,
                        "timeframe": tf,
                        "verdict_label": "no data",
                        "verdict": "HOLD",
                    }
                )
                continue
            alerts = bundle.get("alerts", [])
            fwd = bundle.get("summary", {}).get("forward_stats", {})
            stats = {
                band: ForwardStats(**data)
                for band, data in (fwd.get("net", {}) or {}).items()
            }
            # Prefer the CONVERGENT band; fall back to the highest-N band.
            band = "CONVERGENT"
            st = stats.get(band) or (max(stats.values(), key=lambda s: s.n) if stats else None)
            if st is None or not st.n:
                rows.append(
                    {
                        "symbol": sym,
                        "timeframe": tf,
                        "verdict_label": "no data",
                        "verdict": "HOLD",
                    }
                )
                continue

            live_m = live.get(f"{sym}|{tf}", {})
            pnl = (st.mean_return * amt) if st.mean_return is not None else None
            risk = (st.risk * amt) if st.risk is not None else None

            histogram = _histogram(alerts, band) if isinstance(alerts, list) else []

            if (
                st.mean_return is not None
                and pnl > 0
                and (st.win_rate or 0) >= 0.55
                and (st.risk is None or st.mean_return > st.risk)
            ):
                verdict, verdict_label = "TRADE", "trade"
            elif pnl is not None and pnl > 0:
                verdict, verdict_label = "WATCH", "watch"
            else:
                verdict, verdict_label = "HOLD", "hold"

            rows.append(
                {
                    "symbol": sym,
                    "timeframe": tf,
                    "rank": 0,
                    "live_price": live_m.get("live_price"),
                    "regime": live_m.get("regime"),
                    "band": st.band,
                    "sample": st.n,
                    "win_rate": st.win_rate,
                    "mean_24h": st.mean_return,
                    "risk_24h": st.risk,
                    "histogram": histogram,
                    "projected_pnl": _fmt(pnl),
                    "risk_usd": _fmt(risk),
                    "version": "net",
                    "cost_round_trip": self._cost_model.round_trip,
                    "verdict": verdict,
                    "verdict_label": verdict_label,
                }
            )

        rows.sort(key=lambda r: (r.get("projected_pnl") is not None, r.get("projected_pnl") or 0), reverse=True)
        for i, r in enumerate(rows, start=1):
            r["rank"] = i
        return {"amount": amt, "markets": rows}

    def walkforward(self, fraction: float = 0.30, blocking: bool = True) -> dict:
        """Run the Step-1 gate on NET returns: does the edge survive unseen data + costs?

        Returns a StepResult as a dict. Uses the cost-adjusted `net_24h` per alert,
        so a band only PASSES if its win-rate/mean hold out-of-sample *and* after
        the round-trip cost is deducted. This is the strict go/no-go check before
        any paper-trading or real execution.

        Cached with the same TTL as the alert bundles: the OOS gate is expensive
        (it replays every market's alerts) and its verdict barely moves intra-day,
        so the dashboard never pays the full cost on every page load.

        When `blocking=False` and the cache is missing/stale, returns the *last*
        known-good result if one exists, or a minimal "pending" result rather than
        recomputing inline (the dashboard renders instantly and refreshes via the
        polling endpoints / background pre-warm).
        """
        from .forward import _field, horizon_for
        from .walkforward import run_walkforward

        cache_path = self._cache_dir / "walkforward.json"
        if cache_path.exists() and self._cache_fresh(cache_path):
            return json.loads(cache_path.read_text(encoding="utf-8"))

        if not blocking:
            if cache_path.exists():
                try:
                    return json.loads(cache_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    pass
            return {
                "fraction": fraction,
                "status": "pending",
                "message": "walkforward not computed yet; refresh shortly",
                "bands": {},
                "overall": {},
                "net_of_costs": True,
                "costs": self._cost_model.to_dict(),
            }

        bundles = []
        for ds in self._datasets:
            sym, tf = ds["symbol"], ds["timeframe"]
            bundle = self.compute_one(sym, tf)
            horizon = horizon_for(tf)
            r_field = _field("realized", horizon)
            n_field = _field("net", horizon)
            alerts = []
            for a in bundle.get("alerts", []):
                net = a.get(n_field)
                if net is None:
                    net = self._cost_model.net_return(a.get(r_field))
                alerts.append({**a, r_field: net})
            bundles.append({"symbol": sym, "timeframe": tf, "alerts": alerts})
        result = run_walkforward(bundles, fraction=fraction).to_dict()
        result["net_of_costs"] = True
        result["costs"] = self._cost_model.to_dict()
        try:
            cache_path = self._cache_dir / "walkforward.json"
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - never fail the gate on a cache write
            logger.warning("could not persist walkforward cache: %s", exc)
        return result

    @staticmethod
    def _enrich(alert: SetupAlert, trades_by_setup: Dict[str, object]) -> dict:
        """Attach the setup's eventual resolution to the alert dict.

        outcome      : how the setup resolved overall (SetupOutcome)
        trade        : when a trade was triggered, its exit reason + net return
        """
        d = alert.to_dict()
        outcome = d["properties"].get("_outcome")
        d["outcome"] = outcome
        trade = trades_by_setup.get(d["setup_key"])
        d["trade"] = (
            {
                "exit_reason": trade.exit_reason,
                "net_return": trade.net_return,
                "entry_ts": int(trade.entry_ts),
                "exit_ts": int(trade.exit_ts),
            }
            if trade is not None
            else None
        )
        if trade is not None:
            d["won"] = d["outcome"] == "COMPLETED" and trade.net_return > 0.0
            d["exit_reason"] = trade.exit_reason
        else:
            d["won"] = d["outcome"] == "QUALIFIED_NO_TRADE"
            d["exit_reason"] = None
        return d

    @staticmethod
    def _by_outcome(enriched: List[dict]) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for d in enriched:
            key = d.get("outcome") or "UNKNOWN"
            out[key] = out.get(key, 0) + 1
        return out

    @staticmethod
    def _by_level(alerts: List[SetupAlert]) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for a in alerts:
            out[a.level] = out.get(a.level, 0) + 1
        return out

    # -- Cache -------------------------------------------------------------

    def load_cached(self, symbol: str, timeframe: str) -> Optional[dict]:
        """Public, non-blocking cache read for one market's alert bundle.

        Never runs the engine; returns None when no cache exists on disk. Used
        by efficiency/projection helpers so page loads never pay a history
        replay just to show a number.
        """
        return self._load_compute_free(symbol, timeframe)

    def _load_compute_free(self, symbol: str, timeframe: str) -> Optional[dict]:
        """Load a cached bundle from disk *without running the engine*.

        Used by cache-first endpoints (dashboard / portfolio): the on-disk cache
        is served fresh or stale so the page never blocks on a history replay.
        A missing cache returns None (caller reports the dataset as pending).
        The forward-return enrichment is fast (reads candles, no backtest), so
        it may still be applied to bring a stale bundle up to date.
        """
        cache_path = self._cache_path(symbol, timeframe)
        if not cache_path.exists():
            return None
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        data["alerts"] = sorted(
            data.get("alerts", []), key=lambda a: a.get("timestamp") or 0, reverse=True
        )
        data["cached"] = True
        data["cache_age_s"] = self._cache_age(cache_path)
        try:
            self._ensure_forward(data, symbol, timeframe)
        except Exception:  # noqa: BLE001 - enrichment is best-effort
            pass
        return data

    def _load_if_cached(self, symbol: str, timeframe: str) -> Optional[dict]:
        """Non-blocking cache load for portfolio ranking (never recomputes)."""
        return self._load_compute_free(symbol, timeframe)

    def _cache_path(self, symbol: str, timeframe: str) -> Path:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        safe = symbol.replace("/", "-")
        return self._cache_dir / f"alerts-{safe}-{timeframe}.json"

    def _cache_fresh(self, path: Path) -> bool:
        return self._cache_age(path) < self._cache_ttl_s

    def _cache_ready(self, symbol: str, timeframe: str) -> bool:
        path = self._cache_path(symbol, timeframe)
        return path.exists() and self._cache_fresh(path)

    @staticmethod
    def _cache_age(path: Path) -> float:
        return time.time() - path.stat().st_mtime

    def _persist(self, path: Path, bundle: dict) -> None:
        payload = {k: v for k, v in bundle.items() if k != "cache_age_s"}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")