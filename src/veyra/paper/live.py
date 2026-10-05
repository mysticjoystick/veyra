"""Step 3 — Realtime paper trading checkpoint engine (SIMULATION ONLY).

This is the first place Veyra meets *live, real-time* prices without money.
Unlike the offline ``PaperEngine`` (which replays a frozen history all the way
through to a final ledger), this engine is a *checkpoint*: on each call it —

  1. refreshes the newest live candles via ``LiveScan`` and reads what setups
     are forming *right now* (real Binance data, no money);
  2. projects each live setup's expectation from that band's net track record
     (fees/slippage already deducted), over the dataset's settle horizon
     (24h for 4H/1D, short for 15m);
  3. opens a paper position at the next bar open (or interest area) sized to a
     volatile-aware risk budget (stop = max(structural, ATR*k); notional =
     risk_usd / stop_pct, capped at the caller's amount), and carries it
     forward on subsequent checkpoints;
  4. closes a position when its horizon elapses — or earlier if a live bar
     breaches its stop/invalidation — and books the realized result;
  5. marks still-open positions to the live price and persists a ``paper_live``
     ledger of every decision and its outcome.

It NEVER places a real order. There is no brokerage, no API key, no execution
path, and no chance to touch money. Every number is labelled simulation.

Honesty rules honoured across the whole engine:
  * only TRADE-quality bands (P&L>0, win-rate>=55%, mean>risk) take a position;
  * every realized return is net of the round-trip cost (fees+slippage);
  * projected win-rates / returns are estimates, never a guarantee; the ledger
    records exactly what actually happened, so a weak edge shows up immediately.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from ..alerts.costs import CostModel
from ..alerts.forward import ForwardReturnModel, _fmt
from ..alerts.live import LiveScan
from ..config import Settings, get_settings
from ..data.timeframe import timeframe_interval_seconds
from .quality import (
    PRIOR_N,
    evaluate_eligibility,
    gate_rejects,
    posterior_stats,
)

logger = logging.getLogger(__name__)

# Stage 4 selectivity: only positive-expectancy setup types per market/timeframe,
# plus side filters (ETH SHORT was negative on both 4H and 1D). These were
# validated on 19815 candles of 4H and 3306 candles of 1D in walk-forward.
_STAGE4_ALLOWED_TYPES = {
    ("BTC/USDT", "4H"): {"BREAKOUT", "BREAKOUT_RETEST", "PULLBACK", "TREND_CONTINUATION", "RANGE_REJECTION"},
    ("BTC/USDT", "1D"): {"BREAKOUT", "BREAKOUT_RETEST", "PULLBACK", "TREND_CONTINUATION", "RANGE_REJECTION"},
    ("ETH/USDT", "4H"): {"BREAKOUT_RETEST", "PULLBACK", "TREND_CONTINUATION", "RANGE_REJECTION"},
    ("ETH/USDT", "1D"): {"PULLBACK", "RANGE_REJECTION"},
}
_STAGE4_ALLOWED_SIDE = {
    "ETH/USDT": "LONG",
    "BTC/USDT": None,
}
# Per-(setup_type) side selectivity for markets where the walk-forward evidence
# supports only one side for some types (UNI/USDT: uncovered both, but
# BREAKOUT_RETEST only SHORT and TREND_CONTINUATION only LONG).
_STAGE4_ALLOWED_TYPE_SIDES = {
    ("UNI/USDT", "4H"): {
        "BREAKOUT": {"LONG", "SHORT"},
        "BREAKOUT_RETEST": {"SHORT"},
        "TREND_CONTINUATION": {"LONG"},
    },
}
# Stage 4 drop-set: 15m is not tradeable (negative expectancy on both).
_STAGE4_DATASETS = [
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
    {"symbol": "UNI/USDT", "timeframe": "4H"},
]

_SCHEMA_REV = "paper-live-v1"
_START_CASH = 10_000.0

# Bayesian quality-gate priors live in ``.quality`` (single source of truth):
# a band with zero observed history evaluates to the prior itself, which never
# passes the 0.55 win-rate floor -> nothing opens purely on cold start.


def _stage4_pass(symbol: str, timeframe: str, setup_type: str, side: str) -> bool:
    """Apply Stage 4 setup-type + side selectivity (validated, walk-forward)."""
    allowed = _STAGE4_ALLOWED_TYPES.get((symbol, timeframe))
    if allowed is not None and setup_type not in allowed:
        return False
    type_sides = _STAGE4_ALLOWED_TYPE_SIDES.get((symbol, timeframe))
    if type_sides is not None and side not in type_sides.get(setup_type, set()):
        return False
    side_limit = _STAGE4_ALLOWED_SIDE.get(symbol)
    if side_limit is not None and side != side_limit:
        return False
    return True


def _horizon_seconds(timeframe: str) -> int:
    from ..alerts.forward import horizon_for

    return horizon_for(timeframe)


def fmt_log(agg: dict) -> str:
    """One compact line per checkpoint pass for a projected-vs-realized time series."""
    import time as _time

    markets = agg.get("markets") or []
    per_mkt = ";".join(
        f"{m.get('symbol')}/{m.get('timeframe')}:{len(m.get('open') or [])}o:{len(m.get('closed') or [])}c"
        for m in markets if not m.get("error")
    )
    return (
        f"{_time.strftime('%Y-%m-%d %H:%M:%S')} | "
        f"trades={agg.get('trades', 0)} wins={agg.get('wins', 0)} "
        f"win_rate={_fmt(agg.get('win_rate'))} "
        f"open={agg.get('open_positions', 0)} "
        f"realized={agg.get('sum_pnl', 0)} projected={agg.get('sum_projected', 0)} "
        f"| {per_mkt}"
    )


def _band_for(score: int) -> str:
    # One canonical ladder shared with alerts/dashboard: CONVERGENT>=75,
    # DIRECTIONAL>=55, EMERGENT>=35, else SCANNED. works on the canonical
    # 0-100 score; identical everywhere.
    from ..alerts.models import AlertLevel

    return AlertLevel.for_score(score).name


def _side_bias(side: str) -> float:
    return 1.0 if side == "LONG" else -1.0


def _stop_from_setup(
    side: str,
    entry_price: float,
    invalidation: Optional[float],
    atr: Optional[float] = None,
    atr_multiplier: float = 2.0,
    fallback_pct: float = 0.02,
) -> Optional[float]:
    """Volatility-aware stop (loss) level for a paper position.

    stop_distance = max(structural_distance, ATR * atr_multiplier), where the
    structural distance comes from the setup's own invalidation level when it
    is numeric and on the correct side. When neither resolves, falls back to a
    fixed fraction of the entry price.

    This makes the stop meaningful across markets/timeframes: a 2% stop on BTC
    1H and BTC 4H are deliberately different because the ATR is different.

    The live setup's ``invalidation`` is often a human-readable string (not a
    numeric level), so it is coerced defensively; a non-numeric value just
    contributes no structural distance.
    """
    try:
        if invalidation is not None:
            invalidation = float(invalidation)
    except (TypeError, ValueError):
        invalidation = None
    if entry_price is None or entry_price <= 0:
        return None
    bias = _side_bias(side)

    structural = 0.0
    if invalidation is not None and invalidation > 0:
        if (bias > 0 and invalidation < entry_price) or (bias < 0 and invalidation > entry_price):
            structural = abs(entry_price - invalidation)

    stop_distance = structural
    if atr is not None and atr > 0:
        stop_distance = max(stop_distance, atr * atr_multiplier)
    if stop_distance > 0:
        return entry_price * (1.0 - bias * (stop_distance / entry_price))
    return entry_price * (1.0 - bias * fallback_pct)


def _cold_scale(n: Optional[int], thresholds: tuple = (20, 50)) -> float:
    """Position-scale factor by band observation count (cold-start protection).

    A band with little observed history trades at reduced risk; a fully
    validated band trades at the full per-trade budget.
    """
    if n is None or n < thresholds[0]:
        return 0.25
    if n < thresholds[1]:
        return 0.50
    return 1.00


@dataclass
class LivePaperPosition:
    symbol: str
    timeframe: str
    side: str
    band: str
    entry_ts: int
    entry_price: float
    notional: float
    status: str = "open"  # open | closed
    stop_price: Optional[float] = None
    projected_return: Optional[float] = None
    projected_pnl: Optional[float] = None
    exit_ts: Optional[int] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    gross_return: Optional[float] = None
    fees: float = 0.0
    net_return: Optional[float] = None
    pnl: Optional[float] = None
    mark_price: Optional[float] = None
    opened_at: int = 0
    cold_start: bool = False  # opened on thin/zero band evidence (sized smaller)
    n_observed_at_decision_time: Optional[int] = None  # band evidence seen at entry

    @property
    def unrealized_pnl(self) -> Optional[float]:
        if self.mark_price is None or self.entry_price in (None, 0):
            return None
        raw = (self.mark_price - self.entry_price) / self.entry_price
        return self.notional * _side_bias(self.side) * raw

    def stopped_out(self, bar_low: Optional[float], bar_high: Optional[float]) -> bool:
        """True when a live bar breaches this position's stop level.

        LONG positions stop out when price falls to/below the stop floor;
        SHORT positions stop out when price rises to/above the stop ceiling.
        """
        if self.status != "open" or self.stop_price is None:
            return False
        if _side_bias(self.side) > 0:  # LONG
            return bar_low is not None and bar_low <= self.stop_price
        return bar_high is not None and bar_high >= self.stop_price

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "side": self.side,
            "band": self.band,
            "status": self.status,
            "entry_ts": self.entry_ts,
            "entry_price": self.entry_price,
            "notional": self.notional,
            "stop_price": _fmt(self.stop_price),
            "projected_return": _fmt(self.projected_return),
            "projected_pnl": _fmt(self.projected_pnl),
            "mark_price": _fmt(self.mark_price),
            "exit_ts": self.exit_ts,
            "exit_price": _fmt(self.exit_price),
            "exit_reason": self.exit_reason,
            "gross_return": _fmt(self.gross_return),
            "fees": _fmt(self.fees),
            "net_return": _fmt(self.net_return),
            "pnl": _fmt(self.pnl),
            "unrealized_pnl": _fmt(self.unrealized_pnl),
            "opened_at": self.opened_at,
            "cold_start": self.cold_start,
            "n_observed_at_decision_time": self.n_observed_at_decision_time,
        }


@dataclass
class LivePaperLedger:
    symbol: str
    timeframe: str
    strategy_version: str
    schema: str = _SCHEMA_REV
    start_cash: float = _START_CASH
    cash: float = _START_CASH
    positions: List[LivePaperPosition] = field(default_factory=list)
    trades: List[LivePaperPosition] = field(default_factory=list)
    check_ct: int = 0
    last_check_ts: int = 0

    def unclosed(self) -> List[LivePaperPosition]:
        return [p for p in self.positions if p.status == "open"]

    def is_holding(self, band: str) -> bool:
        return any(p.status == "open" and p.band == band for p in self.positions)

    def to_dict(self, costs: dict) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "strategy_version": self.strategy_version,
            "schema": self.schema,
            "simulation": True,
            "cost_model": costs,
            "start_cash": self.start_cash,
            "cash": self.cash,
            "check_ct": self.check_ct,
            "last_check_ts": self.last_check_ts,
            "positions": [p.to_dict() for p in self.positions],
            "trades": [t.to_dict() for t in self.trades],
        }


class LivePaperEngine:
    """Checkpoint realtime paper trader (simulation only)."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        scan: Optional[LiveScan] = None,
        cost_model: Optional[CostModel] = None,
        ledger_dir: Optional[Path] = None,
        cost_tracker=None,
    ) -> None:
        self._settings = settings or get_settings()
        self._scan = scan or LiveScan(self._settings)
        self._cost = cost_model or CostModel()
        self._ledger_dir = ledger_dir or (self._settings.absolute_data_dir / "paper_live")
        self._ledger_dir.mkdir(parents=True, exist_ok=True)
        self._cost_tracker = cost_tracker

    def _ledger_path(self, symbol: str, timeframe: str) -> Path:
        sym = symbol.replace("/", "").replace("-", "")
        return self._ledger_dir / f"paper_live_{sym}_{timeframe}.json"

    def load(self, symbol: str, timeframe: str) -> LivePaperLedger:
        path = self._ledger_path(symbol, timeframe)
        if not path.exists():
            return LivePaperLedger(symbol=symbol, timeframe=timeframe, strategy_version="veyra-live-v1")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read paper ledger %s: %s", path.name, exc)
            return LivePaperLedger(symbol=symbol, timeframe=timeframe, strategy_version="veyra-live-v1")

        ledger = LivePaperLedger(
            symbol=raw.get("symbol", symbol),
            timeframe=raw.get("timeframe", timeframe),
            strategy_version=raw.get("strategy_version", "veyra-live-v1"),
            start_cash=float(raw.get("start_cash", _START_CASH)),
            cash=float(raw.get("cash", _START_CASH)),
            check_ct=int(raw.get("check_ct", 0)),
            last_check_ts=int(raw.get("last_check_ts", 0)),
        )
        for d in raw.get("positions", []):
            ledger.positions.append(self._position_from_dict(d))
        for d in raw.get("trades", []):
            ledger.trades.append(self._position_from_dict(d))
        return ledger

    @staticmethod
    def _position_from_dict(d: dict) -> LivePaperPosition:
        return LivePaperPosition(
            symbol=d.get("symbol", ""),
            timeframe=d.get("timeframe", ""),
            side=d.get("side", ""),
            band=d.get("band", ""),
            entry_ts=int(d.get("entry_ts", 0)),
            entry_price=float(d.get("entry_price") or 0),
            notional=float(d.get("notional") or 0),
            status=d.get("status", "open"),
            stop_price=d.get("stop_price"),
            projected_return=d.get("projected_return"),
            projected_pnl=d.get("projected_pnl"),
            exit_ts=d.get("exit_ts"),
            exit_price=d.get("exit_price"),
            exit_reason=d.get("exit_reason"),
            gross_return=d.get("gross_return"),
            fees=float(d.get("fees") or 0),
            net_return=d.get("net_return"),
            pnl=d.get("pnl"),
            mark_price=d.get("mark_price"),
            opened_at=int(d.get("opened_at", 0)),
            cold_start=bool(d.get("cold_start", False)),
            n_observed_at_decision_time=d.get("n_observed_at_decision_time"),
        )

    def run_checkpoint(
        self,
        symbol: str,
        timeframe: str,
        amount: float = 1_000.0,
        refresh: bool = True,
        live: Dict[str, object] = None,
        portfolio: Optional[object] = None,
        portfolio_open: Optional[List[dict]] = None,
        portfolio_correlations: Optional[dict] = None,
    ) -> LivePaperLedger:
        """One realtime checkpoint for a market.

        ``portfolio`` / ``portfolio_open`` / ``portfolio_correlations`` are the
        cross-market risk context provided by ``run_all`` (see
        ``paper/portfolio_risk.py``); ``None`` disables the portfolio layer,
        which keeps single-market callers and older call sites unchanged.

        Returns the updated ledger (persisted). Offline-safe: if live candles
        can't be fetched, the engine uses the newest stored tail and marks the
        check accordingly — it never crashes the dashboard or the CLI.
        """
        live = live or self._scan.live(symbol, timeframe, refresh=refresh)
        candles = self._scan._store.load(symbol, timeframe)  # noqa: SLF001 - shared tail
        from ..alerts.forward import _field, horizon_for

        horizon = horizon_for(timeframe)
        model = ForwardReturnModel(candles, horizon=horizon)

        ledger = self.load(symbol, timeframe)
        now = int(time.time())

        setups = [s for s in (live.get("setups") or []) if _is_qualified(s)]

        # 1. Build per-band stats from the market's own alerts for honest grading.
        alerts = self._market_alerts(symbol, timeframe)
        stats = {
            band: st
            for band, st in model.stats_by_band(alerts, field=_field("net", horizon)).items()
        }

        # 2. Close positions whose 24h horizon elapsed (or who hit their stop).
        self._settle_expired(ledger, model, now)

        # 3. Project live setups; open a paper position for a TRADE-quality band.
        self._open_new(ledger, symbol, timeframe, model, stats, setups, amount, now,
                       portfolio=portfolio, portfolio_open=portfolio_open,
                       portfolio_correlations=portfolio_correlations)

        # 3b. Stop-out: close any position whose stop was breached on a live bar.
        self._check_stopouts(ledger, candles, now)

        # 4. Mark remaining open positions to the live price.
        self._mark_open(ledger, live.get("live_price"))

        ledger.check_ct += 1
        ledger.last_check_ts = now
        self._persist(ledger)
        return ledger

    def run_all(
        self,
        datasets: Optional[List[Dict[str, str]]] = None,
        amount: float = 1_000.0,
        refresh: bool = True,
    ) -> dict:
        """Run a checkpoint for every market in one pass (Step-3 scheduler).

        Returns a combined summary so a single scheduled call covers all four
        markets and persists each ledger. Cheap: one live scan + one cache hit
        per market. Offline-safe: a failing market is reported, never fatal.

        Defaults to the Stage 4 market set (4H + 1D; 15m is dropped as it had
        negative expectancy on both BTC and ETH in walk-forward).
        """
        markets = datasets or _STAGE4_DATASETS
        from .portfolio_risk import PortfolioRiskPolicy

        policy = (
            PortfolioRiskPolicy(self._settings)
            if hasattr(self, "_settings") and self._settings else None
        )
        correlations: Optional[dict] = None
        if policy is not None:
            try:
                correlations = policy.compute_correlations(
                    markets, store=self._scan._store, days=30
                )
            except Exception as exc:  # noqa: BLE001 - offline/short history: skip corr
                logger.warning("portfolio correlation unavailable: %s", exc)
        results = []
        for ds in markets:
            symbol, timeframe = ds["symbol"], ds["timeframe"]
            try:
                # Fresh cross-market book BEFORE this market is evaluated, so the
                # cooldown/exposure/correlation rules see positions opened by any
                # market earlier in this same pass.
                if policy is not None:
                    try:
                        open_book = policy.open_book(
                            [self.load(m["symbol"], m["timeframe"]) for m in markets]
                        )
                    except Exception as exc:  # noqa: BLE001 - ledger unreadable: no book
                        logger.warning("portfolio book unavailable: %s", exc)
                        open_book = []
                else:
                    open_book = []
                ledger = self.run_checkpoint(
                    symbol, timeframe,
                    amount=amount,
                    refresh=refresh,
                    portfolio=policy,
                    portfolio_open=open_book,
                    portfolio_correlations=correlations,
                )
                rep = self.report(symbol, timeframe)
                rep["check_ct"] = ledger.check_ct
                rep["last_check_ts"] = ledger.last_check_ts
                results.append(rep)
            except Exception as exc:  # noqa: BLE001
                logger.warning("paper-live run_all failed for %s %s: %s",
                               symbol, timeframe, exc)
                results.append(
                    {"symbol": symbol, "timeframe": timeframe, "error": str(exc),
                     "simulation": True}
                )
        return self._aggregate(results)

    @staticmethod
    def _aggregate(results: List[dict]) -> dict:
        trades = sum(int(r.get("trades") or 0) for r in results)
        wins = sum(int(r.get("wins") or 0) for r in results)
        return {
            "simulation": True,
            "markets": results,
            "trades": trades,
            "wins": wins,
            "win_rate": (wins / trades) if trades else None,
            "sum_pnl": _fmt(sum((r.get("sum_pnl") or 0) for r in results)),
            "sum_projected": _fmt(sum((r.get("sum_projected") or 0) for r in results)),
            "open_positions": sum(len(r.get("open") or []) for r in results),
            "note": "simulation only — no real orders, no money",
        }

    def _market_alerts(self, symbol: str, timeframe: str) -> List[dict]:
        try:
            from ..alerts import AlertService

            return AlertService(self._settings).compute_one(symbol, timeframe).get("alerts", [])
        except Exception as exc:  # noqa: BLE001 - never block paper run
            logger.warning("Could not load market alerts for %s %s: %s", symbol, timeframe, exc)
            return []

    def _settle_expired(
        self,
        ledger: LivePaperLedger,
        model: ForwardReturnModel,
        now: int,
    ) -> None:
        still_open = []
        for pos in ledger.positions:
            if pos.status != "open":
                continue
            horizon = _horizon_seconds(pos.timeframe)
            if now - pos.entry_ts < horizon:
                still_open.append(pos)
                continue
            # Horizon elapsed -> close at the close of the candle at/after horizon.
            exit_price = model._price_after(pos.entry_ts, horizon)  # noqa: SLF001
            if exit_price is None:
                # Not enough live forward data yet; keep it open, mark next check.
                still_open.append(pos)
                continue
            self._close_position(pos, exit_price, exit_ts=pos.entry_ts + horizon,
                                 exit_reason="horizon")
            ledger.trades.append(pos)
        ledger.positions = still_open

    def _check_stopouts(
        self,
        ledger: LivePaperLedger,
        candles,
        now: int,
    ) -> None:
        """Close any open position whose stop was breached on a live bar.

        Walks bars strictly after each position's entry. The first bar whose
        low (LONG) or high (SHORT) crosses the stop level stops the position
        out at the stop price. No live bars, no stop -> nothing happens.
        """
        if candles is None:
            return
        try:
            df = candles.sort_values("open_time")
        except Exception:  # noqa: BLE001 - offline tolerance
            return
        if df.empty:
            return
        times = df["open_time"].tolist()
        lows = df["low"].tolist()
        highs = df["high"].tolist()

        still_open = []
        for pos in ledger.positions:
            if pos.status != "open" or pos.stop_price is None:
                still_open.append(pos)
                continue
            # Inspect bars opened after the entry bar.
            breach_ts = None
            breach_price = pos.stop_price
            for i in range(len(times)):
                if times[i] <= pos.entry_ts:
                    continue
                bar_low = lows[i]
                bar_high = highs[i]
                bias = _side_bias(pos.side)
                crossed = (
                    (bias > 0 and bar_low is not None and bar_low <= pos.stop_price)
                    or (bias < 0 and bar_high is not None and bar_high >= pos.stop_price)
                )
                if crossed:
                    breach_ts = times[i]
                    break
            if breach_ts is None:
                still_open.append(pos)
                continue
            self._close_position(pos, breach_price, exit_ts=int(breach_ts),
                                 exit_reason="stop")
            ledger.trades.append(pos)
        ledger.positions = still_open

    def _open_new(
        self,
        ledger: LivePaperLedger,
        symbol: str,
        timeframe: str,
        model: ForwardReturnModel,
        stats: Dict[str, object],
        setups: List[dict],
        amount: float,
        now: int,
        portfolio: Optional[object] = None,
        portfolio_open: Optional[List[dict]] = None,
        portfolio_correlations: Optional[dict] = None,
    ) -> None:
        for s in setups:
            band = _band_for(int(s.get("overall_score") or 0))
            if ledger.is_holding(band):
                continue
            # Stage 4 selectivity: exclude losing setup-types/sides per market.
            if not _stage4_pass(
                symbol, timeframe,
                str(s.get("setup_type") or ""), str(s.get("side") or "LONG"),
            ):
                continue
            st = stats.get(band)
            # Thin-evidence flag (position sizing, NOT a gate bypass): a band
            # with fewer observed returns than the prior's pseudo-count trades
            # smaller. The quality gate itself never bypasses for thin evidence.
            cold_start = st is None or not st.n or (st.n < PRIOR_N)
            # Single eligibility contract: band ladder + Bayesian posterior gate.
            elig = evaluate_eligibility(int(s.get("overall_score") or 0), st)
            if not elig["trade_eligible"]:
                continue
            # Quantified decision gate (shared with alerts/dashboard): the
            # posterior alone can pass on tiny samples when risk is None, so
            # require the full TRADE verdict — sample floor, raw edge, R:R >=
            # 1.0, efficiency confirmation, no live contradiction. Anything
            # else is monitored, never paper-traded.
            try:
                from ..decision import decide as _decide
                from ..efficiency import efficiency_score as _eff_score

                _live_rets = [
                    t.net_return for t in ledger.trades
                    if getattr(t, "net_return", None) is not None
                ]
                _proj = None
                if st is not None:
                    _proj = {
                        "n": getattr(st, "n", 0),
                        "win_rate": getattr(st, "win_rate", None),
                        "mean_return": getattr(st, "mean_return", None),
                        "risk": getattr(st, "risk", None),
                    }
                _eff = _eff_score(_proj, _live_rets)
                _dec = _decide(
                    int(s.get("overall_score") or 0), st, _eff,
                    setup=s, live_returns=_live_rets,
                )
                if _dec.get("verdict") != "TRADE":
                    logger.info(
                        "decision-gate rejected %s %s %s: %s (%s)",
                        symbol, timeframe, s.get("side") or "LONG",
                        _dec.get("verdict"), "; ".join(_dec.get("reasons") or []),
                    )
                    continue
            except Exception as exc:  # noqa: BLE001 - gate must never crash paper
                logger.warning("decision gate unavailable: %s", exc)
                continue
            entry_ts = int(s.get("timestamp") or now)
            entry_price = _next_open_or_area(s, model)
            if entry_price is None or entry_price <= 0:
                entry_price = _store_at_close(
                    self._scan._store, symbol, timeframe, entry_ts  # noqa: SLF001
                )
            if entry_price is None or entry_price <= 0:
                entry_price = _area_mid(s.get("interest_area"))
            if entry_price is None or entry_price <= 0:
                continue
            projected_return = st.mean_return if st else None
            n_observed = st.n if st is not None else 0
            atr = s.get("atr")
            stop_price = _stop_from_setup(
                str(s.get("side") or "LONG"), entry_price,
                s.get("invalidation"),
                atr=atr,
                atr_multiplier=self._settings.paper_atr_stop_multiplier,
                fallback_pct=self._settings.paper_stop_fallback_pct,
            )
            # Risk-based position sizing: each position stands to lose no more
            # than `risk_usd` at its stop distance (`entry -> stop`). Thinly
            # evidenced bands (fewer than the prior's pseudo-observations) trade
            # at reduced risk; fully validated bands at the full per-trade budget.
            risk_usd = self._settings.paper_risk_per_trade * _cold_scale(
                n_observed,
                thresholds=self._settings.paper_cold_tier_thresholds,
            )
            stop_pct = (
                abs(entry_price - stop_price) / entry_price
                if (stop_price and entry_price > 0) else 0.0
            )
            notional = amount
            if stop_pct > 1e-6:
                notional = min(amount, risk_usd / stop_pct)
            if notional <= 0:
                continue
            # Portfolio-level risk layer (cross-market cooldown, exposure cap,
            # correlation) is applied by run_all; absent for single-market calls.
            if portfolio is not None:
                result = portfolio.check(
                    symbol=symbol,
                    timeframe=timeframe,
                    side=str(s.get("side") or "LONG"),
                    notional=notional,
                    open_positions=portfolio_open or [],
                    correlations=portfolio_correlations,
                )
                if not result.approved:
                    logger.info(
                        "portfolio-rule rejected %s %s %s notional=%.2f: %s",
                        symbol, timeframe, s.get("side") or "LONG",
                        notional, result.reason,
                    )
                    continue
                notional *= result.size_multiplier
                if notional <= 0:
                    continue
            pos = LivePaperPosition(
                symbol=symbol,
                timeframe=timeframe,
                side=str(s.get("side") or "LONG"),
                band=band,
                entry_ts=entry_ts,
                entry_price=float(entry_price),
                notional=float(notional),
                stop_price=stop_price,
                projected_return=projected_return,
                projected_pnl=(notional * projected_return) if projected_return is not None else None,
                opened_at=now,
                cold_start=cold_start,
                n_observed_at_decision_time=n_observed,
            )
            ledger.positions.append(pos)

    def _mark_open(self, ledger: LivePaperLedger, live_price: Optional[float]) -> None:
        if live_price is None:
            return
        for pos in ledger.positions:
            if pos.status == "open":
                pos.mark_price = float(live_price)

    def _close_position(self, pos: LivePaperPosition, exit_price: float, exit_ts: int,
                        exit_reason: str) -> None:
        pos.exit_price = float(exit_price)
        pos.exit_ts = int(exit_ts)
        raw = (exit_price - pos.entry_price) / pos.entry_price if pos.entry_price else 0
        pos.gross_return = _side_bias(pos.side) * raw
        gross_pnl = pos.notional * pos.gross_return
        pos.fees = pos.notional * self._cost.round_trip
        pos.net_return = _fmt(pos.gross_return - self._cost.round_trip)
        pos.pnl = gross_pnl - pos.fees
        pos.status = "closed"
        pos.exit_reason = exit_reason
        pos.mark_price = float(exit_price)
        if getattr(self, "_cost_tracker", None) is not None:
            try:
                self._cost_tracker.record(pos.symbol, pos.timeframe, pos)
            except Exception as exc:  # noqa: BLE001 - tracking must never break closes
                logger.warning("cost tracker record failed: %s", exc)

    def _persist(self, ledger: LivePaperLedger) -> None:
        path = self._ledger_path(ledger.symbol, ledger.timeframe)
        path.write_text(
            json.dumps(ledger.to_dict(self._cost.to_dict()), indent=2),
            encoding="utf-8",
        )

    def report(self, symbol: str, timeframe: str) -> dict:
        """Render a human/UI summary of the live paper ledger for a market."""
        ledger = self.load(symbol, timeframe)
        open_pos = ledger.unclosed()
        realized = [t for t in ledger.trades]
        wins = [t for t in realized if (t.pnl or 0) > 0]
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "simulation": True,
            "strategy_version": ledger.strategy_version,
            "start_cash": ledger.start_cash,
            "cash": ledger.cash,
            "open": [p.to_dict() for p in open_pos],
            "closed": [t.to_dict() for t in realized],
            "trades": len(realized),
            "wins": len(wins),
            "win_rate": (len(wins) / len(realized)) if realized else None,
            "sum_pnl": _fmt(sum((t.pnl or 0) for t in realized)),
            "sum_projected": _fmt(sum((t.projected_pnl or 0) for t in realized)),
            "note": "simulation only — no real orders, no money",
        }


def _store_at_close(store, symbol: str, timeframe: str, ts: int) -> Optional[float]:
    """Close of the newest stored candle at/before `ts`, or the latest close."""
    try:
        df = store.load(symbol, timeframe)
    except Exception:  # noqa: BLE001 - offline tolerance
        return None
    if df is None or df.empty:
        return None
    df = df.sort_values("open_time")
    past = df[df["open_time"] <= ts]
    row = past.iloc[-1] if not past.empty else df.iloc[-1]
    return float(row["close"])


def _is_qualified(s: dict) -> bool:
    """A live setup must produce a usable band to be paper-tradeable."""
    return _band_for(int(s.get("overall_score") or 0)) != "SCANNED"


def _is_trade_quality(st, *, cold_start: bool = False) -> bool:
    """Posterior-based TRADE rule — no cold-start bypass (see ``.quality``).

    ``cold_start`` is kept only for callers that must *size* thin-evidence bands
    smaller; it never bypasses the gate. Evidence size is recorded per trade as
    ``n_observed_at_decision_time`` for audit.
    """
    return gate_rejects(st) is None


def _area_mid(interest_area: Optional[dict]) -> Optional[float]:
    if not interest_area:
        return None
    low = interest_area.get("low")
    high = interest_area.get("high")
    if low is None or high is None:
        return None
    return (float(low) + float(high)) / 2.0


def _next_open_or_area(s: dict, model: ForwardReturnModel) -> Optional[float]:
    """Entry at the next bar open after detection; else the area midpoint."""
    entry_ts = int(s.get("timestamp") or 0)
    interval = timeframe_interval_seconds(s.get("time", "1D")) or 86400
    next_open_ts = entry_ts + interval
    row = model._row_at_or_after(next_open_ts)  # noqa: SLF001
    if row is not None:
        return float(row["open"])
    return _area_mid(s.get("interest_area"))