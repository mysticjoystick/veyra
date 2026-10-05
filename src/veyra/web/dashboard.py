"""Veyra command-center dashboard (Phase 6 preview).

A small FastAPI app that renders the selective setup alerts produced from the
frozen baseline over real data. It is SIMULATION ONLY: it lists alerts and
allows no trading, no ordering, and no account mutation.

Routes:
    GET /                  rendered dashboard page (Jinja2)
    GET /api/alerts        JSON list of alert bundles (one per dataset)
    GET /api/status        cache + dataset summary

Phase 8: the whole command-center is gated behind an authenticated session.
Unauthenticated visitors are redirected/denied; the operator account is seeded
idempotently from ``VEYRA_ADMIN_EMAIL`` / ``VEYRA_ADMIN_PASSWORD``.

Computation is memoised to data/alerts so page reloads are fast; the first
request builds the cache (running the frozen engine per dataset).
"""

from __future__ import annotations

import json
import logging
import threading as _threading
import time
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Query, Request, WebSocket, WebSocketDisconnect

_DASHBOARD_CACHE_TTL = 60.0
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from .. import __version__
from ..alerts.live import LiveScan
from ..alerts.service import AlertService
from ..auth import AuthService, build_auth_router, require_user
from ..config import get_settings
from ..data.provider.binance_stream import BinanceStream

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
LATEST_DEFAULT = 10

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

_DEFAULT_STREAM_TIMEFRAMES = ["1D", "4H", "1H"]
_DEFAULT_STREAM_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT")
_stream_singleton: Optional[BinanceStream] = None


# Core 4 markets for instant mobile paint (BTC/ETH x 4H/1D). Full 12 load on demand.
CORE_DATASETS = [
    {"symbol": "BTC/USDT", "timeframe": "4H"},
    {"symbol": "BTC/USDT", "timeframe": "1D"},
    {"symbol": "ETH/USDT", "timeframe": "4H"},
    {"symbol": "ETH/USDT", "timeframe": "1D"},
]


def _slice_datasets(datasets: list, limit: int | None) -> list:
    """Return first `limit` datasets; None/0 means all. Never raises."""
    try:
        if limit is None or limit <= 0:
            return list(datasets)
        return list(datasets)[: max(1, min(int(limit), len(datasets)))]
    except Exception:  # noqa: BLE001
        return list(datasets)


def _get_shared_stream(symbols: list, timeframes: list) -> BinanceStream:
    """Return a lazily-created, single shared Binance Stream instance."""
    global _stream_singleton
    if _stream_singleton is None:
        _stream_singleton = BinanceStream()
    return _stream_singleton


async def _stream_loop(
    scan: LiveScan,
    stream: BinanceStream,
    websocket: WebSocket,
    symbol: str,
    timeframe: str,
) -> None:
    """Send an initial REST snapshot, then stream live candle ticks.

    The Binance feed is started on first use. When the feed has a live candle
    for the market, we forward it; when it does not (socket down), we fall back
    to slow REST polling so the connection still yields updates.
    """
    import asyncio

    from fastapi import WebSocketDisconnect

    try:
        snapshot = await asyncio.to_thread(scan.chart, symbol, timeframe, 80, True)
        snapshot["kind"] = "snapshot"
        await websocket.send_json(snapshot)
    except Exception as exc:  # noqa: BLE001
        await websocket.send_json({"kind": "error", "message": str(exc)})

    # Ensure the shared Binance feed is subscribed for this market (idempotent).
    try:
        stream.start(list(_DEFAULT_STREAM_SYMBOLS), _DEFAULT_STREAM_TIMEFRAMES)
    except Exception:  # noqa: BLE001
        pass

    last = None
    poll = 0
    while True:
        try:
            candle = await asyncio.to_thread(stream.candle, symbol, timeframe)
        except Exception:  # noqa: BLE001
            candle = None
        changed = candle is not None and last != candle
        if changed:
            await websocket.send_json({"kind": "tick", "candle": candle})
            last = candle
            poll = 0
        else:
            # Fall back to REST refresh when no live tick is flowing.
            poll += 1
            if poll >= 30:
                try:
                    snap = await asyncio.to_thread(scan.chart, symbol, timeframe, 80, True)
                    await websocket.send_json({"kind": "snapshot", "candles": snap.get("candles", []), "live_price": snap.get("live_price")})
                except Exception:  # noqa: BLE001
                    pass
                poll = 0
        try:
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            break


def create_dashboard(
    service: Optional[AlertService] = None,
    live_scan: Optional[LiveScan] = None,
    template_dir: Path = _TEMPLATE_DIR,
    auth_service: Optional[AuthService] = None,
    subscription_service: Optional[object] = None,
    runner: Optional[object] = None,
) -> FastAPI:
    """Factory so tests can inject stub services (no heavy compute).

    ``auth_service`` is optional for tests; when omitted it is built from the
    environment settings (DB session factory + idempotent admin seed).
    ``subscription_service`` defaults to a real Phase 9 entitlement service
    bound to the same DB, so /api/session can report entitlement status.

    ``runner`` (optional) is a ``LiveAlertRunner`` started on app startup so
    the dashboard shows the live Telegram alert cadence / countdown. Tests
    omit it so no background polling starts.
    """
    from contextlib import asynccontextmanager

    svc = service or AlertService()
    scan = live_scan or LiveScan()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if runner is not None:
            runner.start()
        yield
        if runner is not None:
            runner.stop()

    # Pre-warm the heavy step-1 gate cache on startup so the very first page
    # load is fast instead of paying a full history replay synchronously.
    # Non-blocking: serves existing (even stale) caches instantly and recomputes
    # in the background only when truly missing.
    def _prewarm_walkforward() -> None:
        try:
            svc.compute_all(force=False, blocking=False)
            svc.walkforward(0.30, blocking=False)
        except Exception as exc:  # noqa: BLE001 - startup must never crash
            logger.warning("walkforward pre-warm failed: %s", exc)

    try:
        import threading as _threading

        _w = _threading.Thread(target=_prewarm_walkforward, name="veyra-prewarm", daemon=True)
        _w.start()
    except Exception:  # noqa: BLE001
        pass

    app = FastAPI(
        title="Veyra Dashboard",
        version=__version__,
        description="Veyra selective setup-alert dashboard (signals only)",
        lifespan=lifespan,
    )
    # Perf: gzip HTML/JSON so mobile 4G pays ~1/4 bytes. Cheap, no behavior change.
    try:
        from fastapi.middleware.gzip import GZipMiddleware

        app.add_middleware(GZipMiddleware, minimum_size=1024)
    except Exception:  # noqa: BLE001 - compression is best-effort
        pass
    templates = Jinja2Templates(directory=str(template_dir))
    _fmt_dt = _make_dt_filter()
    templates.env.filters["dt"] = _fmt_dt
    templates.env.globals["outcome_badge"] = _render_outcome_badge
    templates.env.globals["clarity"] = _clarity_for

    # PWA: public install + offline-shell surface (no auth so Chrome can
    # fetch the manifest/icons/SW before login and show the Install prompt).
    _STATIC_DIR = Path(__file__).resolve().parent / "static"
    try:
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles

        if _STATIC_DIR.exists():
            app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="veyra-static")

        @app.get("/manifest.webmanifest", include_in_schema=False)
        def pwa_manifest():
            path = _STATIC_DIR / "manifest.webmanifest"
            return FileResponse(str(path), media_type="application/manifest+json", headers={"Cache-Control": "public, max-age=86400"})

        @app.get("/sw.js", include_in_schema=False)
        def pwa_sw():
            path = _STATIC_DIR / "sw.js"
            return FileResponse(str(path), media_type="application/javascript", headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})

        @app.get("/icons/{name}", include_in_schema=False)
        def pwa_icon(name: str):
            safe = {"icon-192.png", "icon-512.png", "apple-touch-icon.png"}
            if name not in safe:
                from fastapi import HTTPException as _HE

                raise _HE(status_code=404, detail="unknown icon")
            return FileResponse(str(_STATIC_DIR / "icons" / name), media_type="image/png", headers={"Cache-Control": "public, max-age=31536000, immutable"})
    except Exception:  # noqa: BLE001 - PWA is additive, never breaks dashboard
        logger.warning("PWA static mount skipped")

    # Per-app page cache: keeps repeated renders instant without ever sending
    # one app's (or test's) data to another.
    app.state.dashboard_cache = {"context": None, "ts": 0.0}

    # Phase 8 auth: gate the whole command-center behind an admin session.
    if auth_service is None:
        auth_service = _build_auth_service_from_settings()
    app.state.auth = auth_service
    app.include_router(build_auth_router(templates))

    # Redirect browser requests to /login on unauthenticated access.
    # require_user raises HTTPException(401); this middleware converts those
    # to 303 redirects for browser paths (HTML page requests).
    from ..auth.router import _auth_redirect_middleware
    _auth_redirect_middleware(app)

    # Phase 9: attach subscription entitlements (per-account). Admin/paid
    # accounts are entitled to paid features such as Telegram alerts.
    app.state.subscriptions = (
        subscription_service
        if subscription_service is not None
        else _build_subscription_service()
    )

    @app.get("/", response_class=HTMLResponse)
    def dashboard(
        request: Request,
        force: int = Query(default=0, ge=0, le=1, description="Force full recompute (may be slow)"),
        shell: int = Query(default=0, ge=0, le=1, description="Shell-only instant paint; JS hydrates via APIs"),
        markets: int = Query(default=0, ge=0, le=12, description="Limit server-rendered markets (0=all, 4=core mobile set)"),
        _user: object = Depends(require_user),
    ) -> HTMLResponse:
        import time as _time
        now = _time.time()
        page_cache = app.state.dashboard_cache
        cache_key = f"{shell}|{markets}|{force}"
        cached_entry = page_cache.get(cache_key)

        if not force and cached_entry and now - cached_entry["ts"] < _DASHBOARD_CACHE_TTL:
            resp = templates.TemplateResponse(request, "dashboard.html", cached_entry["context"])
            resp.headers["Cache-Control"] = "private, max-age=30"
            resp.headers["X-Veyra-Shell"] = str(shell)
            return resp

        # Shell-first: skip the 2x live_all pipeline scans (12 x 400-bar each)
        # and serve cache-only aggregates. Client hydrates live data via
        # /api/lifecycle (single poll) after first paint. ~80% server time saved.
        wanted = _slice_datasets(DEFAULT_DATASETS, markets if markets else None)
        payload = svc.compute_all(force=False, blocking=False)
        # Filter payload to wanted markets so ?markets=4 renders a small page.
        try:
            _want_keys = {f"{d['symbol']}|{d['timeframe']}" for d in wanted}
            _all_ds = payload.get("datasets", [])
            payload = {**payload, "datasets": [d for d in _all_ds if f"{d.get('symbol')}|{d.get('timeframe')}" in _want_keys] or _all_ds}
        except Exception:  # noqa: BLE001
            pass
        latest = svc.latest(LATEST_DEFAULT)

        if shell:
            live_markets: list = []
            portfolio = {"amount": 1000.0, "markets": []}
            walkforward = None
            efficiency_map = {}
        else:
            # Default (non-shell) still stays fast: server-render only the core
            # 4 live cards (BTC/ETH 4H/1D); the full 12-market pipeline hydrates
            # via /api/lifecycle after first paint. Cuts 12x400-bar scans to 4x.
            _core_live = wanted[:4] if len(wanted) > 4 else wanted
            try:
                live_markets = scan.live_all(_core_live, refresh=False)
            except Exception:
                live_markets = []

            try:
                portfolio = svc.portfolio(amount=1000.0, scan=scan, refresh=False, blocking=False)
            except Exception:
                portfolio = {"amount": 1000.0, "markets": []}

            try:
                walkforward = svc.walkforward(fraction=0.30, blocking=False)
            except Exception:
                walkforward = None

            efficiency_map = _build_efficiency_map(live_markets)
        runner_status = runner.status() if runner is not None else {
            "running": False, "markets": [], "configured": False, "gated": False}
        context = _render_context(
            payload,
            strategy_version=_strategy_version(svc),
            latest=latest,
            live=live_markets,
            portfolio=portfolio,
            walkforward=walkforward,
            efficiency_map=efficiency_map,
            runner_status=runner_status,
        )
        # Tell the template it is a shell so it can render skeletons + hydrate.
        context["is_shell"] = bool(shell)
        context["markets_limit"] = markets or 0

        page_cache[cache_key] = {"context": context, "ts": now}
        # Keep legacy key for tests that poke app.state.dashboard_cache["context"].
        page_cache["context"] = context
        page_cache["ts"] = now

        def _bg_refresh():
            try:
                # Light refresh only: live tail (cached) + cache-first aggregates.
                # Full history replay (blocking=True) is CLI-only (`veyra alert`)
                # — running it here on every page expiry pegged CPU for 10s+.
                scan.live_all(wanted, refresh=False)
                svc.compute_all(force=False, blocking=False)
                svc.portfolio(amount=1000.0, scan=scan, refresh=False, blocking=False)
                svc.walkforward(fraction=0.30, blocking=False)
            except Exception:
                pass

        try:
            _t = _threading.Thread(target=_bg_refresh, daemon=True)
            _t.start()
        except Exception:
            pass

        resp = templates.TemplateResponse(request, "dashboard.html", context)
        resp.headers["Cache-Control"] = "private, max-age=30"
        resp.headers["X-Veyra-Shell"] = str(shell)
        return resp

    @app.get("/api/alerts")
    def api_alerts(
        force: int = Query(default=0, ge=0, le=1),
        _user: object = Depends(require_user),
    ) -> JSONResponse:
        return JSONResponse(svc.compute_all(force=bool(force), blocking=False))

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        """Liveness probe for uptime monitors / load balancers (no auth)."""
        return JSONResponse({"status": "ok", "name": "veyra", "version": __version__})

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        """Readiness probe: DB + candle store reachable (no auth, cheap)."""
        try:
            settings = get_settings()
            db_ok = settings.absolute_database_path.parent.exists()
            cs_ok = settings.absolute_candle_store_dir.exists()
            ok = bool(db_ok or cs_ok)
            return JSONResponse({"ready": ok, "db_parent_exists": db_ok, "candle_store_exists": cs_ok})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ready": False, "error": str(exc)})

    @app.get("/api/live")
    def api_live(
        markets: int = Query(default=0, ge=0, le=12, description="Limit markets (0=all, 4=core mobile set)"),
        _user: object = Depends(require_user),
    ) -> JSONResponse:
        """Fast real-time scan over the latest candle tail (no history replay)."""
        wanted = _slice_datasets(DEFAULT_DATASETS, markets if markets else None)
        resp = JSONResponse(
            {"markets": scan.live_all(wanted, refresh=False), "note": "signals only"}
        )
        resp.headers["Cache-Control"] = "private, max-age=15"
        return resp

    @app.get("/api/efficiency")
    def api_efficiency(_user: object = Depends(require_user)) -> JSONResponse:
        """Live, self-improving efficiency score per market (evidence-weighted).

        Blends the band's realized forward track record with the accumulating
        live paper trade results. The score climbs as real trades complete.
        Never a guarantee - an honest evidence-weighted estimate.
        """
        from ..efficiency import efficiency_for_market

        markets = scan.live_all(DEFAULT_DATASETS, refresh=False)
        result = []
        for m in markets:
            symbol = m.get("symbol")
            timeframe = m.get("timeframe")
            setups = m.get("setups") or []
            score = int(setups[0].get("overall_score") or m.get("overall_score") or 0) if setups else int(m.get("overall_score") or 0)
            eff = efficiency_for_market(symbol, timeframe, score, get_settings())
            result.append({
                "symbol": symbol,
                "timeframe": timeframe,
                "clarity_score": score,
                **eff,
            })
        top = max(result, key=lambda r: r.get("score") or 0) if result else None
        return JSONResponse({
            "markets": result,
            "best": top,
            "note": "live Binance observation; self-improving as real trades complete.",
        })

    @app.get("/api/runner")
    def api_runner(_user: object = Depends(require_user)) -> JSONResponse:
        """Status of the embedded Telegram alert runner (cadence + countdown)."""
        if runner is None:
            return JSONResponse({"running": False, "markets": [],
                                 "note": "runner not embedded in this app"})
        return JSONResponse(runner.status())

    @app.get("/api/latest")
    def api_latest(
        n: int = Query(default=10, ge=1, le=50),
        _user: object = Depends(require_user),
    ) -> JSONResponse:
        return JSONResponse(
            {
                "latest": svc.latest(n),
                "note": "live view over cached replays (signals only)",
            }
        )

    @app.get("/api/opportunity")
    def api_opportunity(
        symbol: str = Query(default="BTC/USDT"),
        timeframe: str = Query(default="1D"),
        amount: Optional[float] = Query(default=None, ge=0, description="size you'd trade (e.g. 1000)"),
        _user: object = Depends(require_user),
    ) -> JSONResponse:
        """Projected 24h return + P&L estimate for a dataset (honest, historical)."""
        try:
            return JSONResponse(svc.project(symbol, timeframe, amount=amount))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"symbol": symbol, "timeframe": timeframe, "error": str(exc)})

    @app.get("/api/portfolio")
    def api_portfolio(
        amount: Optional[float] = Query(default=None, ge=0, description="trade size"),
        markets: int = Query(default=0, ge=0, le=12, description="Limit markets (0=all)"),
        _user: object = Depends(require_user),
    ) -> JSONResponse:
        """Best-right-now ranking of all markets by projected 24h P&L."""
        try:
            data = svc.portfolio(amount=amount, blocking=False)
            if markets:
                try:
                    data = {**data, "markets": (data.get("markets") or [])[: int(markets)]}
                except Exception:  # noqa: BLE001
                    pass
            resp = JSONResponse(data)
            resp.headers["Cache-Control"] = "private, max-age=30"
            return resp
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)})

    @app.get("/api/chart")
    def api_chart(
        symbol: str = Query(default="BTC/USDT"),
        timeframe: str = Query(default="4H"),
        limit: int = Query(default=80, ge=10, le=500),
        refresh: int = Query(default=1, ge=0, le=1),
        _user: object = Depends(require_user),
    ) -> JSONResponse:
        """Realtime OHLC candles + live price for a live candlestick chart."""
        try:
            return JSONResponse(scan.chart(symbol, timeframe, limit=limit, refresh=bool(refresh)))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"symbol": symbol, "timeframe": timeframe, "error": str(exc)})

    @app.get("/api/walkforward")
    def api_walkforward(
        fraction: float = Query(default=0.30, gt=0, lt=1),
        _user: object = Depends(require_user),
    ) -> JSONResponse:
        """Step-1 out-of-sample gate: does the 24h edge survive unseen data?"""
        try:
            return JSONResponse(svc.walkforward(fraction=fraction, blocking=False))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc)})

    @app.get("/api/paper-live")
    def api_paper_live(
        symbol: str = Query(default="BTC/USDT"),
        timeframe: str = Query(default="4H"),
        amount: float = Query(default=1000.0, gt=0),
        refresh: bool = Query(default=True),
        _user: object = Depends(require_user),
    ) -> JSONResponse:
        """Step-3 realtime paper checkpoint (simulation only)."""
        try:
            from ..paper.live import LivePaperEngine

            engine = LivePaperEngine()
            ledger = engine.run_checkpoint(symbol, timeframe, amount=amount, refresh=refresh)
            return JSONResponse(engine.report(symbol, timeframe))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"symbol": symbol, "timeframe": timeframe, "error": str(exc), "simulation": True})

    @app.get("/api/paper-live-all")
    def api_paper_live_all(
        amount: float = Query(default=1000.0, gt=0),
        refresh: bool = Query(default=True),
        _user: object = Depends(require_user),
    ) -> JSONResponse:
        """Step-3 realtime paper checkpoint across all markets (scheduler)."""
        try:
            from ..paper.live import LivePaperEngine

            return JSONResponse(LivePaperEngine().run_all(amount=amount, refresh=refresh))
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": str(exc), "simulation": True})

    @app.get("/api/closed-trades")
    def api_closed_trades(
        _user: object = Depends(require_user),
    ) -> JSONResponse:
        """All closed live-paper trades across every market, newest first."""
        try:
            from ..paper.live import LivePaperEngine

            engine = LivePaperEngine()
            all_trades = []
            for ds in DEFAULT_DATASETS:
                symbol, tf = ds["symbol"], ds["timeframe"]
                ledger = engine.load(symbol, tf)
                for t in ledger.trades:
                    d = t.to_dict()
                    d["_sort"] = t.exit_ts or 0
                    all_trades.append(d)
            all_trades.sort(key=lambda x: x["_sort"], reverse=True)
            for t in all_trades:
                t.pop("_sort", None)
            return JSONResponse({"trades": all_trades, "total": len(all_trades)})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"trades": [], "total": 0, "error": str(exc)})

    @app.get("/api/lifecycle")
    def api_lifecycle(
        markets: int = Query(default=0, ge=0, le=12, description="Limit markets (0=all, 4=core mobile set)"),
        _user: object = Depends(require_user),
    ) -> JSONResponse:
        """Trade lifecycle: live setups -> open positions -> settled -> published.

        One aggregated payload for the dashboard's pipeline view. Combines the
        realtime scan, the live paper ledger (open + closed), the running
        efficiency map and the postback publishing state (X draft / Bluesky /
        Telegram) so a single poll re-renders the whole pipeline.
        """
        try:
            from ..paper.live import LivePaperEngine

            payload = _lifecycle_payload(
                LivePaperEngine(get_settings()),
                scan, svc, get_settings(),
                datasets=_slice_datasets(DEFAULT_DATASETS, markets if markets else None),
            )
            resp = JSONResponse(payload)
            resp.headers["Cache-Control"] = "private, max-age=15"
            return resp
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"markets": [], "stats": {}, "error": str(exc)})

    # Shared Binance WebSocket feed, started lazily on first WS client.
    stream = _get_shared_stream([d["symbol"] for d in DEFAULT_DATASETS], _DEFAULT_STREAM_TIMEFRAMES)

    @app.websocket("/ws/chart")
    async def ws_chart(websocket: WebSocket) -> None:
        """Stream live kline ticks for a market over a WebSocket.

        Client sends JSON "subscribe": {"symbol": "...", "timeframe": "..."}.
        Server first pushes a REST snapshot, then forwards realtime candle
        updates as Binance ticks arrive. Falls back to slow polling if the
        Binance socket is disconnected (stream stays usable, just slightly
        less "instant").
        """
        # WebSocket dependencies can't use FastAPI's Cookie/Request injection
        # the same way HTTP routes do, so we check auth manually from the
        # raw cookie before accepting.
        from ..auth.router import SESSION_COOKIE, get_auth_service
        token = websocket.cookies.get(SESSION_COOKIE)
        service = get_auth_service(websocket)
        user = service.resolve_session(token) if token else None
        if user is None:
            await websocket.close(code=4001)
            return

        await websocket.accept()
        symbol = "BTC/USDT"
        timeframe = "4H"
        try:
            first = await websocket.receive_json()
            symbol = first.get("symbol", symbol)
            timeframe = first.get("timeframe", timeframe)
        except Exception:  # noqa: BLE001
            pass

        await _stream_loop(scan, stream, websocket, symbol, timeframe)

    @app.get("/api/status")
    def api_status(_user: object = Depends(require_user)) -> JSONResponse:
        return JSONResponse(
            {
                "name": "veyra",
                "version": __version__,
                "strategy_version": _strategy_version(svc),
                "datasets": [d for d in svc.datasets()],
                "now": int(time.time()),
            }
        )

    @app.get("/api/settings")
    def api_settings(_user: object = Depends(require_user)) -> JSONResponse:
        """Safe, read-only runtime configuration for the Settings tab.

        Shows operational flags and boundaries. Never exposes secrets.
        """
        settings = get_settings()
        run = runner.status() if runner is not None else {
            "running": False, "markets": [], "configured": False, "gated": False, "send": False
        }
        return JSONResponse({
            "name": "veyra",
            "version": __version__,
            "strategy_version": _strategy_version(svc),
            "datasets": [d for d in svc.datasets()],
            "worker": {
                "poll_s": 60,
                "refresh_hint": "pipeline & scanner refresh every 60s; alerts/history on every poll",
            },
            "runner": {
                "running": bool(run.get("running")),
                "interval": run.get("interval"),
                "cadence_label": run.get("markets", [{}])[0].get("cadence_label") if run.get("markets") else "n/a",
                "configured": bool(run.get("configured")),
                "gated": bool(run.get("gated")),
                "send": bool(run.get("send")),
            },
            "postback": {
                "x": bool(settings.postback_x_enabled),
                "bluesky": bool(settings.postback_bluesky_enabled),
                "telegram": bool(settings.postback_telegram_enabled),
            },
            "telegram": {
                "configured": bool(settings.telegram_bot_token and settings.telegram_chat_id),
                "require_subscription": bool(settings.telegram_require_subscription),
                "dry_run": False,
            },
            "boundaries": {
                "signals_only": True,
                "order_routing": False,
                "exchange_credentials": False,
            },
        })

    return app


def _render_context(
    payload: dict,
    strategy_version: str = "unknown",
    latest: Optional[list] = None,
    live: Optional[list] = None,
    portfolio: Optional[dict] = None,
    walkforward: Optional[dict] = None,
    efficiency_map: Optional[dict] = None,
    runner_status: Optional[dict] = None,
) -> dict:
    datasets = payload.get("datasets", [])
    total_alerts = sum(d.get("alert_count") or 0 for d in datasets)
    total_qualified = sum(d.get("qualified") or 0 for d in datasets)
    portfolio = portfolio or {"amount": None, "markets": []}
    return {
        "title": "Veyra — selective setup alerts",
        "signals_only": True,
        "strategy_version": strategy_version,
        "datasets": datasets,
        "total_alerts": total_alerts,
        "total_qualified": total_qualified,
        "latest": latest or [],
        "live": live or [],
        "portfolio": portfolio,
        "walkforward": walkforward,
        "efficiency_map": efficiency_map or {},
        "runner_status": runner_status or {"running": False, "markets": []},
        "last_error": payload.get("last_error"),
        "generated_at": int(time.time()),
    }


def _build_efficiency_map(live_markets: list) -> dict:
    """Best-effort map of {symbol|timeframe: efficiency summary} for the page.

    Never raises - the page must not fail because an efficiency estimate wasn't
    available for a market.
    """
    from ..efficiency import efficiency_for_market

    out = {}
    for m in live_markets or []:
        symbol = m.get("symbol")
        timeframe = m.get("timeframe")
        if not symbol or not timeframe:
            continue
        try:
            # Use the top setup's clarity score for the band (matches the alert
            # path), falling back to the market-level overall score.
            setups = m.get("setups") or []
            score = int(setups[0].get("overall_score") or m.get("overall_score") or 0) if setups else int(m.get("overall_score") or 0)
            eff = efficiency_for_market(symbol, timeframe, score, get_settings())
            eff["clarity_score"] = score
            out[f"{symbol}|{timeframe}"] = eff
        except Exception:  # noqa: BLE001 - page must not fail on efficiency
            out[f"{symbol}|{timeframe}"] = {
                "score": 0, "win_rate": None, "expectancy": None,
                "live_sample": 0, "confidence": "VERY LOW", "n": 0,
                "skill_band": "cold",
            }
    return out


_db_factory_cache = None


def _lifecycle_payload(engine, scan, svc, settings, datasets=None) -> dict:
    """Aggregate the full trade lifecycle for the dashboard pipeline view.

    Never raises on a missing file or ledger: the page must not fail because a
    market hasn't traded yet or a state file was rewritten mid-copy. Every
    number comes from the real paper ledger / live scan / published state.
    """
    from ..alerts.models import AlertLevel

    wanted = list(datasets) if datasets is not None else list(DEFAULT_DATASETS)
    live_markets = scan.live_all(wanted, refresh=False)
    live_map = {f"{m.get('symbol')}|{m.get('timeframe')}": m for m in live_markets}
    eff_map = _build_efficiency_map(live_markets)

    from ..marketing.postback import _load_snapshot as _load_eff_snapshot

    portfolio_rows = {}
    try:
        for _m in svc.portfolio(amount=1000.0, scan=scan, blocking=False).get("markets", []):
            portfolio_rows[f"{_m.get('symbol')}|{_m.get('timeframe')}"] = _m
    except Exception:  # noqa: BLE001 - predictions must never break the page
        portfolio_rows = {}

    snapshots: dict = {}
    for ds in wanted:
        key = f"{ds['symbol']}|{ds['timeframe']}"
        snap = _load_eff_snapshot(settings, ds["symbol"], ds["timeframe"])
        if snap:
            snapshots[key] = snap

    published: set = set()
    pb_path = settings.absolute_data_dir / "postback" / "published.json"
    if pb_path.exists():
        try:
            published = set(json.loads(pb_path.read_text(encoding="utf-8")).get("published", []))
        except (OSError, ValueError):
            published = set()

    drafts = set()
    pdir = settings.absolute_data_dir / "postback"
    if pdir.exists():
        for f in pdir.glob("*.txt"):
            # e.g. 001_2026-09-05_08-03-11_ETH_USDT_4H_LONG.txt
            parts = f.stem.split("_")
            if len(parts) >= 5 and parts[-1] in ("LONG", "SHORT"):
                drafts.add(("/".join(parts[3:-2]), parts[-1].upper()))

    markets = []
    open_total = closed_total = wins = 0
    sum_pnl = 0.0
    all_closed: list = []
    for ds in wanted:
        symbol, tf = ds["symbol"], ds["timeframe"]
        try:
            ledger = engine.load(symbol, tf)
        except Exception:  # noqa: BLE001
            ledger = None
        open_pos = []
        closed = []
        if ledger is not None:
            open_pos = [p.to_dict() for p in ledger.unclosed()]
            closed = [t.to_dict() for t in ledger.trades]
            for c in closed:
                c["_sort"] = c.get("exit_ts") or 0
            closed.sort(key=lambda x: x["_sort"], reverse=True)
            for c in closed:
                c.pop("_sort", None)
        open_total += len(open_pos)
        closed_total += len(closed)
        wins += sum(1 for c in closed if (c.get("pnl") or 0) > 0)
        sum_pnl += sum((c.get("pnl") or 0) for c in closed)
        all_closed.extend(closed)

        live = live_map.get(f"{symbol}|{tf}") or {}
        setups = live.get("setups") or []
        top = setups[0] if setups else None
        score_val = (top or {}).get("overall_score")
        if score_val is None:
            score_val = live.get("overall_score")
        live_score = int(score_val) if score_val is not None else None

        latest_close = closed[0] if closed else None
        publish = _publish_state(latest_close, symbol, tf, published, drafts, settings)

        proj = portfolio_rows.get(f"{symbol}|{tf}") or {}

        eff = snapshots.get(f"{symbol}|{tf}") or eff_map.get(f"{symbol}|{tf}") or {}
        band = None
        if live_score is not None:
            band = AlertLevel.for_score(live_score).name
        # Quantified decision per setup (TRADE / WATCH / STAND_ASIDE).
        # Best-effort: a weak setup is still shown for transparency, but
        # labelled STAND_ASIDE with its blocking reason — never as tradable.
        decision = None
        try:
            from ..decision import verdict_for_market as _verdict_for_market
            from ..live_runner import _live_trade_returns as _live_rets
            from ..live_runner import _projection_rows as _proj_rows

            _rows = _proj_rows(symbol, tf)
            _live_tr = _live_rets(symbol, tf)
            for s in setups:
                try:
                    _ctx = dict(s or {})
                    _ctx.setdefault("time", tf)
                    _ctx.setdefault("timeframe", tf)
                    s["decision"] = _verdict_for_market(
                        int(s.get("overall_score") or 0), _rows, _live_tr, setup=_ctx
                    )
                except Exception:  # noqa: BLE001 - one bad setup never breaks page
                    s["decision"] = {
                        "verdict": "STAND_ASIDE", "reasons": ["decision_unavailable"],
                    }
            if top is not None:
                decision = (top.get("decision") or {})
            elif live_score is not None:
                decision = _verdict_for_market(live_score, _rows, _live_tr, setup=None)
        except Exception:  # noqa: BLE001 - decision is display-only
            decision = decision or None
        markets.append({
            "symbol": symbol,
            "timeframe": tf,
            "live_score": live_score,
            "live_band": band,
            "live_price": live.get("live_price"),
            "decision": decision,
            "context": {
                "regime": live.get("regime"),
                "regime_reason": live.get("regime_reason") or "",
                "freshness": live.get("freshness"),
                "analysed_bars": live.get("analysed_bars"),
                "deep_enough": live.get("deep_enough"),
                "bar": live.get("bar"),
            },
            "preview": live.get("preview") or [],
            "setups": setups,
            "projection": {
                "verdict": proj.get("verdict_label") or proj.get("verdict"),
                "win_rate": proj.get("win_rate"),
                "mean_24h": proj.get("mean_24h"),
                "projected_pnl": proj.get("projected_pnl"),
                "risk_usd": proj.get("risk_usd"),
            },
            "open_positions": open_pos,
            "closed_trades": closed,
            "latest_close": latest_close,
            "efficiency": {
                "score": eff.get("score"),
                "win_rate": eff.get("win_rate"),
                "expectancy": eff.get("expectancy"),
                "live_sample": eff.get("live_sample"),
                "confidence": eff.get("confidence"),
            },
            "publish": publish,
        })

    gross_win = sum((c.get("pnl") or 0) for c in all_closed if (c.get("pnl") or 0) > 0)
    gross_loss = abs(sum((c.get("pnl") or 0) for c in all_closed if (c.get("pnl") or 0) < 0))
    win_trades = [c for c in all_closed if (c.get("pnl") or 0) > 0]
    loss_trades = [c for c in all_closed if (c.get("pnl") or 0) < 0]

    equity = []
    _run = 0.0
    _peak = 0.0
    _max_dd = 0.0
    for c in sorted(all_closed, key=lambda x: x.get("exit_ts") or 0):
        _run += c.get("pnl") or 0
        _peak = max(_peak, _run)
        _dd = _peak - _run
        if _dd > _max_dd:
            _max_dd = _dd
        equity.append({"ts": c.get("exit_ts"), "pnl": round(_run, 2)})

    return {
        "markets": markets,
        "stats": {
            "open_positions": open_total,
            "closed_trades": closed_total,
            "wins": wins,
            "win_rate": (wins / closed_total) if closed_total else None,
            "sum_pnl": round(sum_pnl, 2),
            "avg_win": round(gross_win / len(win_trades), 2) if win_trades else None,
            "avg_loss": round(gross_loss / len(loss_trades), 2) if loss_trades else None,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
            "expectancy": round(sum_pnl / closed_total, 2) if closed_total else None,
            "max_drawdown": round(_max_dd, 2),
            "equity_curve": equity,
        },
        "published": {
            "x": bool(settings.postback_x_enabled),
            "bluesky": bool(settings.postback_bluesky_enabled),
            "telegram": bool(settings.postback_telegram_enabled),
        },
    }


def _publish_state(trade, symbol, tf, published, drafts, settings) -> dict:
    """Which channels a closed trade was published to (x/bsky/tg), honestly."""
    if not trade:
        return {"x": False, "bluesky": False, "telegram": False}
    tid = f"{trade.get('symbol')}|{trade.get('timeframe')}|{trade.get('exit_ts')}|{trade.get('exit_price')}"
    done = tid in published
    side = (trade.get("side") or "").upper()
    return {
        "x": bool(settings.postback_x_enabled) and ((symbol, side) in drafts),
        "bluesky": bool(settings.postback_bluesky_enabled) and done,
        "telegram": bool(settings.postback_telegram_enabled) and done,
    }


def _db_session_factory():
    """Lazily build one SQLAlchemy session factory for the dashboard's DB."""
    global _db_factory_cache
    if _db_factory_cache is None:
        from ..database.engine import build_engine, init_db, make_session_factory

        engine = build_engine(get_settings())
        init_db(engine)
        _db_factory_cache = make_session_factory(engine)
    return _db_factory_cache


def _build_auth_service_from_settings() -> AuthService:
    """Build the auth service from environment settings for the dashboard app.

    Constructs the SQLAlchemy engine, creates all tables, seeds the operator
    admin account (idempotent), and returns an AuthService bound to it.
    """
    settings = get_settings()
    factory = _db_session_factory()
    auth_service = AuthService(factory, session_ttl_seconds=settings.auth_session_ttl_seconds)
    if settings.admin_email and settings.admin_password:
        auth_service.ensure_admin(settings.admin_email, settings.admin_password)
    return auth_service


def _build_subscription_service():
    """Build the Phase 9 entitlement service against the same DB as auth."""
    from ..subscribe import SubscriptionService

    return SubscriptionService(_db_session_factory())


def _strategy_version(svc: AlertService) -> str:
    try:
        from ..phase5.frozen_config import PHASE5_BASELINE_VERSION

        return PHASE5_BASELINE_VERSION
    except Exception:  # noqa: BLE001 - version is cosmetic
        return "unknown"


def _render_outcome_badge(a: dict) -> str:
    """Render a win/loss/no-trade pill for an enriched alert dict."""
    from markupsafe import Markup

    outcome = a.get("outcome")
    won = bool(a.get("won"))
    trade = a.get("trade")
    if trade is not None:
        cls = "win" if won else "loss"
        ret = trade.get("net_return")
        label = f"+{ret*100:.1f}%" if won else f"{ret*100:.1f}%"
        tiny = f"exit {trade.get('exit_reason')}"
    elif outcome == "QUALIFIED_NO_TRADE":
        cls = "nt"
        label = "no trade"
        tiny = "qualified, never triggered"
    else:
        cls = "nt"
        label = str(outcome or "UNKNOWN")
        tiny = "never qualified"
    text = f'<span class="res {cls}">{Markup.escape(label)}<br><span class="tiny">{Markup.escape(tiny)}</span></span>'
    return Markup(text)


def _clarity_for(score):
    """Map a normalised score to its clarity band name for realtime cards."""
    from ..alerts.models import AlertLevel

    return AlertLevel.for_score(int(score) if score is not None else 0).name


def _make_dt_filter():
    import datetime as _dt

    def fmt(epoch_seconds) -> str:
        if not epoch_seconds:
            return "n/a"
        try:
            return _dt.datetime.fromtimestamp(int(epoch_seconds)).strftime(
                "%Y-%m-%d %H:%M"
            )
        except (OverflowError, OSError, ValueError):
            return str(epoch_seconds)

    return fmt


# Convenience module-level app for `uvicorn veyra.web:dashboard_app`.
# It embeds a real LiveAlertRunner so the Telegram alert cadence/countdown is
# live on the dashboard (started on app startup, stopped on shutdown).
from ..live_runner import LiveAlertRunner  # noqa: E402

dashboard_app = create_dashboard(runner=LiveAlertRunner())