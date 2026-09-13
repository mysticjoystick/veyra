"""Veyra continuous live alert scanner (SIMULATION ONLY - never an order).

Scans LIVE Binance candle data continuously (every few minutes around the
clock) and pushes a detailed Telegram alert to the channel whenever a setup
forms / changes state. It watches for good opportunities at any time of day -
it only sends when a setup actually appears or develops (deduped on state
change), so no spam.

Run:
    python -m veyra.live_runner            # all markets, continuous watch
    python -m veyra.live_runner --interval 60   # check every 60s (dev)

It can also run embedded inside the dashboard as a background thread via the
``LiveAlertRunner`` class (started on FastAPI startup), so the dashboard can
report the live watch cadence / countdown to the next Telegram scan.

This NEVER places an order. It reads live data, detects setups, and notifies.
The human decides whether to act. Every alert is labelled "simulation notice
only - never an order".
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .alerts.live import LiveScan
from .config import get_settings
from .efficiency import efficiency_score
from .notify import TelegramNotifier
from .notify.clerk import NotifyClerk, render_message
from .paper.live import _STAGE4_DATASETS

logger = logging.getLogger("veyra.live_runner")

# How often the scanner polls live Binance for every market (seconds).
# NOTE: this is a *continuous watch* cadence, not a candle cadence. `scan_live`
# dedupes on setup STATE change, so polling frequently never spams - it only
# sends a Telegram alert when a setup actually appears / develops / changes.
HEARTBEAT = 60
# Human label for the watch cadence shown on the dashboard.
CADENCE_LABEL = {"_default": f"every {HEARTBEAT}s"}


def _scan_and_notify(settings, scan: LiveScan, clerk: NotifyClerk,
                     notifier: TelegramNotifier, symbol: str, timeframe: str,
                     refresh: bool, amount: float = 1000.0) -> int:
    """Scan one live market and send any state-change alerts. Returns sent count."""
    try:
        live = scan.live(symbol, timeframe, refresh=refresh)
    except Exception as exc:  # noqa: BLE001 - never crash the runner
        logger.warning("scan %s %s failed: %s", symbol, timeframe, exc)
        return 0

    # Band track record (realized forward returns) for honest projections,
    # blended with the accumulating live paper trade results for the score.
    proj_rows = _projection_rows(symbol, timeframe)
    live_trades = _live_trade_returns(symbol, timeframe)
    rows = clerk.scan_live([live])
    sent = 0
    for r in rows:
        pri_row = r
        band = _band_for(int(live.get("overall_score") or 0))
        proj = proj_rows.get(band)
        eff = efficiency_score(proj, live_trades)
        msg = render_message(pri_row, projection=proj, amount=amount,
                             timeframe=timeframe, efficiency=eff)
        if notifier.configured and not notifier.gated:
            result = notifier.send(msg)
            if result.get("sent"):
                sent += 1
            logger.info("[sent=%s] %s", result.get("sent"), msg)
        else:
            logger.info("[dry] %s", msg)
            sent += 1
    return sent


def _live_trade_returns(symbol: str, timeframe: str) -> list:
    """Net returns of closed live paper trades for a market (may be empty)."""
    try:
        from .paper.live import LivePaperEngine

        ledger = LivePaperEngine(get_settings()).load(symbol, timeframe)
        return [t.net_return for t in ledger.trades if t.net_return is not None]
    except Exception as exc:  # noqa: BLE001 - never break alerts for score
        logger.warning("live trades for %s %s failed: %s", symbol, timeframe, exc)
        return []


def _band_for(score: int) -> str:
    from .alerts.models import AlertLevel

    return AlertLevel.for_score(score).name


_projection_cache: dict = {}
_PROJECTION_CACHE_TTL_S = 900.0
_projection_svc = None


def _projection_rows(symbol: str, timeframe: str) -> dict:
    """Per-band realized forward stats for a market, as {band: {dict}}.

    Fast and cache-first: reads the on-disk alert bundle (never recomputes the
    engine) and the candle store. A short-lived in-process memo avoids
    re-reading parquet on every dashboard poll. Missing cache degrades to an
    empty dict so callers under-report rather than block.
    """
    import time as _time

    global _projection_svc
    key = f"{symbol}|{timeframe}"
    now = _time.time()
    hit = _projection_cache.get(key)
    if hit is not None and now - hit[0] < _PROJECTION_CACHE_TTL_S:
        return hit[1]

    from .alerts.forward import ForwardReturnModel, _field, horizon_for
    from .alerts import AlertService

    out = {}
    try:
        store = LiveScan(get_settings())._store
        candles = store.load(symbol, timeframe)
        if candles is None or candles.empty:
            return out
        horizon = horizon_for(timeframe)
        model = ForwardReturnModel(candles, horizon=horizon)
        if _projection_svc is None:
            _projection_svc = AlertService(get_settings())
        bundle = _projection_svc.load_cached(symbol, timeframe) or {}
        alerts = bundle.get("alerts", [])
        stats = model.stats_by_band(alerts, field=_field("net", horizon))
        for band, st in stats.items():
            out[band] = {
                "n": st.n,
                "win_rate": st.win_rate,
                "mean_return": st.mean_return,
                "risk": st.risk,
            }
        _projection_cache[key] = (now, out)
    except Exception as exc:  # noqa: BLE001 - projection must never break alerts
        logger.warning("projection for %s %s failed: %s", symbol, timeframe, exc)
        _projection_cache[key] = (now, out)
    return out


class LiveAlertRunner:
    """A self-contained, threadable Telegram alert runner.

    Can be started standalone (its own ``main``) or embedded in the dashboard
    as a background thread. Exposes a thread-safe ``status()`` snapshot so a UI
    can show the live cadence / countdown to the next scan per market.
    """

    def __init__(self, settings=None, interval: int = HEARTBEAT,
                 markets=None, amount: float = 1000.0):
        self._settings = settings or get_settings()
        self._interval = max(60, interval)
        self._amount = amount
        self._markets = markets or [
            (d["symbol"], d["timeframe"]) for d in _STAGE4_DATASETS
        ]

        self._scan = LiveScan(self._settings)
        self._clerk = NotifyClerk()
        self._notifier = TelegramNotifier(
            bot_token=self._settings.telegram_bot_token or "",
            chat_id=self._settings.telegram_chat_id or "",
            dry_run=False,
            require_entitlement=self._settings.telegram_require_subscription,
            entitled=True,
        )
        # Live paper engine shares the same LiveScan/CandleStore so there is a
        # single writer to the candle files (prevents cross-process corruption)
        # and papers trades open/close so the efficiency score updates in
        # realtime as real setups trigger and resolve.
        from .paper.live import LivePaperEngine

        self._paper = LivePaperEngine(self._settings, scan=self._scan)

        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

        # Per-market stats tracked for the dashboard.
        self._stats = {
            f"{s}|{t}": {
                "symbol": s,
                "timeframe": t,
                "cadence_s": self._interval,
                "cadence_label": CADENCE_LABEL["_default"],
                "next_check_ts": None,
                "last_check_ts": None,
                "last_scan_at": None,
                "alerts_sent": 0,
                "last_message": None,
                "last_error": None,
                "open_positions": 0,
                "closed_trades": 0,
            }
            for s, t in self._markets
        }
        self._started_at = None
        self._running = False

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if self._running:
            return
        self._stop.clear()
        self._started_at = int(time.time())
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name="veyra-live-runner", daemon=True
        )
        self._thread.start()
        logger.info("LiveAlertRunner started")

    def stop(self) -> None:
        self._stop.set()
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    # -- worker -----------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            for symbol, timeframe in self._markets:
                if self._stop.is_set():
                    return
                self._run_market(symbol, timeframe)
            self._stop.wait(self._interval)

    def _run_market(self, symbol: str, timeframe: str) -> None:
        key = f"{symbol}|{timeframe}"
        now = int(time.time())
        try:
            live = self._scan.live(symbol, timeframe, refresh=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("scan %s %s failed: %s", symbol, timeframe, exc)
            with self._lock:
                self._stats[key]["last_error"] = str(exc)
                self._stats[key]["last_check_ts"] = now
                self._stats[key]["next_check_ts"] = now + self._interval
            return

        proj_rows = _projection_rows(symbol, timeframe)
        live_trades = _live_trade_returns(symbol, timeframe)
        rows = self._clerk.scan_live([live])
        sent = 0
        last_msg = None
        for r in rows:
            band = _band_for(int(live.get("overall_score") or 0))
            proj = proj_rows.get(band)
            eff = efficiency_score(proj, live_trades)
            msg = render_message(r, projection=proj, amount=self._amount,
                                 timeframe=timeframe, efficiency=eff)
            last_msg = msg
            if self._notifier.configured and not self._notifier.gated:
                result = self._notifier.send(msg)
                if result.get("sent"):
                    sent += 1
            else:
                sent += 1
            logger.info("[sent=%s] %s", sent, msg[:120])

        # Real-time paper checkpoint: opens positions on qualifying setups and
        # settles them on stop / horizon so the live ledger grows and the
        # efficiency score updates trade-over-trade. Reuses the scan result so
        # the candle store is touched only once per pass (no extra refresh).
        open_pos = closed = 0
        newly_closed: list = []
        try:
            from .marketing.postback import PostBacker
            from .paper.live import LivePaperEngine

            prior_closed = len(
                LivePaperEngine(self._settings).load(symbol, timeframe).trades
            )
            ledger = self._paper.run_checkpoint(
                symbol, timeframe, amount=self._amount,
                refresh=False, live=live,
            )
            trades = ledger.trades
            open_pos = sum(1 for p in ledger.positions if p.status == "open")
            closed = len(trades)
            newly_closed = [t.to_dict() for t in trades[prior_closed:]]
        except Exception as exc:  # noqa: BLE001 - paper must never break alerts
            logger.warning("paper checkpoint %s %s failed: %s", symbol, timeframe, exc)

        # Publish any newly settled trade as a ready-to-post X message and/or
        # an auto Bluesky post (both gated by settings; honest, never fake).
        if newly_closed:
            try:
                PostBacker(self._settings, telegram_notifier=self._notifier).handle_closed(
                    symbol, timeframe, newly_closed
                )
            except Exception as exc:  # noqa: BLE001 - never break the runner
                logger.warning("postback %s %s failed: %s", symbol, timeframe, exc)

        with self._lock:
            st = self._stats[key]
            st["last_check_ts"] = now
            st["next_check_ts"] = now + self._interval
            st["last_scan_at"] = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S")
            st["alerts_sent"] = st.get("alerts_sent", 0) + sent
            st["last_error"] = None
            st["open_positions"] = open_pos
            st["closed_trades"] = closed
            if last_msg:
                st["last_message"] = last_msg

    # -- reporting --------------------------------------------------------
    def status(self) -> dict:
        now = int(time.time())
        with self._lock:
            markets = []
            for st in self._stats.values():
                nxt = st.get("next_check_ts")
                markets.append({
                    "symbol": st["symbol"],
                    "timeframe": st["timeframe"],
                    "cadence_label": st["cadence_label"],
                    "last_scan_at": st.get("last_scan_at"),
                    "alerts_sent": st.get("alerts_sent", 0),
                    "last_message": st.get("last_message"),
                    "last_error": st.get("last_error"),
                    "open_positions": st.get("open_positions", 0),
                    "closed_trades": st.get("closed_trades", 0),
                    "seconds_left": max(0, nxt - now) if nxt else None,
                })
            markets = sorted(markets, key=lambda m: (m["symbol"], m["timeframe"]))
        return {
            "running": self._running,
            "started_at": self._started_at,
            "interval": self._interval,
            "configured": self._notifier.configured,
            "gated": self._notifier.gated,
            "send": (not self._notifier._dry_run) and self._notifier.configured,
            "markets": markets,
        }


def main(argv=None) -> int:
    args = argv or sys.argv[1:]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=int, default=HEARTBEAT,
                        help="check interval in seconds (min 60)")
    parser.add_argument("--once", action="store_true",
                        help="run a single pass then exit (for cron/testing)")
    parser.add_argument("--symbol", default=None, help="filter to a symbol")
    parser.add_argument("--timeframe", default=None, help="filter to a timeframe")
    parser.add_argument("--loop", type=int, default=0,
                        help="stop after this many passes (0 = run forever)")
    opt = parser.parse_args(args)

    settings = get_settings()
    # Live scanning always refreshes live Binance data (not simulation replay).
    scan = LiveScan(settings)
    clerk = NotifyClerk()
    notifier = TelegramNotifier(
        bot_token=settings.telegram_bot_token or "",
        chat_id=settings.telegram_chat_id or "",
        dry_run=False,
        require_entitlement=settings.telegram_require_subscription,
        entitled=True,
    )

    markets = [
        (d["symbol"], d["timeframe"])
        for d in _STAGE4_DATASETS
        if (not opt.symbol or d["symbol"] == opt.symbol)
        and (not opt.timeframe or d["timeframe"] == opt.timeframe)
    ]
    if not markets:
        print("No markets matched the filters.")
        return 1

    interval = max(60, opt.interval)
    pass_ct = 0

    print(f"Veyra live runner (live Binance observation, not an order)")
    print(f"  markets : {', '.join(f'{s} {t}' for s, t in markets)}")
    print(f"  interval: {interval}s continuous watch  send: {not notifier._dry_run}  "
          f"configured: {notifier.configured}")
    print()

    try:
        while True:
            now = int(time.time())
            for symbol, timeframe in markets:
                # Continuous watch - poll every market every pass. scan_live
                # dedupes on state change, so this sends only on real change.
                sent = _scan_and_notify(settings, scan, clerk, notifier,
                                        symbol, timeframe, refresh=True)
                if sent:
                    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {symbol} {timeframe}: "
                          f"{sent} alert(s) sent")
            pass_ct += 1
            if opt.loop and pass_ct >= opt.loop:
                break
            if opt.once:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
