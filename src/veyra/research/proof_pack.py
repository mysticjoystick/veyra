"""Proof pack: export verifiable evidence for "can this make real numbers?".

Emit, into ``reports/proof_pack/``:

1. ``trades.csv`` / ``trades.json`` — every settled paper trade across all Stage-4
   ledgers, with entry/exit prices, gross/net return, fees, realized PnL,
   ``n_observed_at_decision_time`` and ``cold_start`` (the evidence level the
   decision actually saw).
2. ``equity_curve.csv`` — cumulative realized PnL per trade, ordered by exit time.
3. ``drawdown.json`` — peak-to-trough, longest losing streak, and recovery from the
   live paper trade sequence (per market and aggregate).
4. ``band_evidence.json`` — per market, per band: raw band stats, posterior win
   rate / mean return, and the exact gate verdict the live engine would apply.
5. ``phase5_summary.json`` — the statistically-sized deep backtests (BTC/ETH 4H+1D):
   IN/VALIDATION/OOS trades, win rate, expectancy, max drawdown, streak, fees.
6. ``cost_tracker.json`` — the running cost log (fees + slippage charged).

The comparison table (paper vs live) is intentionally absent from the artifacts:
there is no live trading — every row here is simulation-only. What a paper ledger
CAN prove is the decision record (evidence seen, gate passed, PnL realized).
"""

from __future__ import annotations

import csv
import glob
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..config import get_settings

OUTDIR = os.path.join("reports", "proof_pack")

_HORIZON_LABEL = {"15m": "15m", "1H": "1h", "4H": "4h", "1D": "1d"}


# --------------------------------------------------------------------------- #
# Small pure helpers (kept local so the exporter needs no engine imports in the
# critical path; the engine is used only for the band-evidence pass).
# --------------------------------------------------------------------------- #


def _fmt_price(v: Optional[float]) -> Optional[float]:
    if v is None:
        return None
    return round(float(v), 8)


@dataclass
class Trade:
    symbol: str
    timeframe: str
    side: str
    band: str
    entry_ts: int
    entry_price: Optional[float]
    notional: float
    stop_price: Optional[float]
    exit_ts: Optional[int]
    exit_price: Optional[float]
    exit_reason: Optional[str]
    gross_return: Optional[float]
    fees: float
    net_return: Optional[float]
    pnl: Optional[float]
    cold_start: bool
    n_observed: Optional[int]

    @classmethod
    def from_dict(cls, d: dict) -> "Trade":
        return cls(
            symbol=d.get("symbol", ""),
            timeframe=d.get("timeframe", ""),
            side=d.get("side", ""),
            band=d.get("band", ""),
            entry_ts=int(d.get("entry_ts") or 0),
            entry_price=_fmt_price(d.get("entry_price")),
            notional=float(d.get("notional") or 0.0),
            stop_price=_fmt_price(d.get("stop_price")),
            exit_ts=int(d.get("exit_ts") or 0) or None,
            exit_price=_fmt_price(d.get("exit_price")),
            exit_reason=d.get("exit_reason"),
            gross_return=float(d["gross_return"]) if d.get("gross_return") is not None else None,
            fees=float(d.get("fees") or 0.0),
            net_return=float(d["net_return"]) if d.get("net_return") is not None else None,
            pnl=float(d["pnl"]) if d.get("pnl") is not None else None,
            cold_start=bool(d.get("cold_start")),
            n_observed=d.get("n_observed_at_decision_time"),
        )

    def as_row(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "side": self.side,
            "band": self.band,
            "entry_ts": self.entry_ts,
            "entry_price": self.entry_price,
            "notional": self.notional,
            "stop_price": self.stop_price,
            "exit_ts": self.exit_ts,
            "exit_price": self.exit_price,
            "exit_reason": self.exit_reason,
            "gross_return": self.gross_return,
            "fees": self.fees,
            "net_return": self.net_return,
            "pnl": self.pnl,
            "cold_start": self.cold_start,
            "n_observed_at_decision_time": self.n_observed,
        }


# --------------------------------------------------------------------------- #
# 1/2/3. Live paper trade log + equity + drawdown.
# --------------------------------------------------------------------------- #


def _load_ledgers(data_dir: str = "data/paper_live") -> List[dict]:
    ledgers = []
    for path in sorted(glob.glob(os.path.join(data_dir, "paper_live_*.json"))):
        with open(path, encoding="utf-8") as fh:
            ledgers.append(json.load(fh))
    return ledgers


def _settled_trades(ledgers: List[dict]) -> List[Trade]:
    trades: List[Trade] = []
    for ledger in ledgers:
        for row in ledger.get("trades") or []:
            trades.append(Trade.from_dict(row))
    trades.sort(key=lambda t: (t.exit_ts or 0, t.entry_ts))
    return trades


def _open_positions(ledgers: List[dict]) -> List[dict]:
    rows = []
    for ledger in ledgers:
        for p in ledger.get("positions") or []:
            if p.get("status") == "open":
                rows.append(p)
    return rows


def _write_csv(path: str, header: List[str], rows: List[Dict[str, Any]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _drawdown_metrics(trades: List[Trade]) -> Dict[str, Any]:
    if not trades:
        return {"trades": 0}
    pnl_series = [t.pnl or 0.0 for t in trades]
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    trough_after_peak = 0.0
    longest_loss = 0
    current_loss = 0
    losses = wins = 0
    for pnl in pnl_series:
        cum += pnl
        if pnl < 0:
            losses += 1
            current_loss += 1
        else:
            wins += 1
            current_loss = 0
        longest_loss = max(longest_loss, current_loss)
        if cum > peak:
            peak = cum
            trough_after_peak = 0.0
        else:
            dd = peak - cum
            trough_after_peak = max(trough_after_peak, dd)
        max_dd = max(max_dd, peak - cum)
    total_pnl = cum
    return {
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / len(trades)) if trades else None,
        "sum_pnl": _fmt_price(total_pnl),
        "final_equity": _fmt_price(_START_CASH + total_pnl) if _START_CASH else None,
        "max_drawdown_usd": _fmt_price(max_dd),
        "longest_losing_streak": longest_loss,
        "avg_win_usd": _fmt_price(sum(p for p in pnl_series if p > 0) / wins) if wins else None,
        "avg_loss_usd": _fmt_price(sum(p for p in pnl_series if p < 0) / losses) if losses else None,
    }


# --------------------------------------------------------------------------- #
# 4. Band evidence (posterior gate the live engine applies).
# --------------------------------------------------------------------------- #


def _band_evidence() -> Dict[str, Any]:
    from ..alerts.forward import _field, horizon_for
    from ..paper.live import _STAGE4_DATASETS, LivePaperEngine
    from ..paper.quality import PRIOR_N, gate_rejects, posterior_stats

    engine = LivePaperEngine(get_settings())
    candle_store = engine._scan._store  # noqa: SLF001 - same node the paper engine grades on

    out: Dict[str, Any] = {"markets": []}
    for ds in _STAGE4_DATASETS:
        symbol, timeframe = ds["symbol"], ds["timeframe"]
        market: Dict[str, Any] = {"symbol": symbol, "timeframe": timeframe, "bands": []}
        try:
            from ..alerts import AlertService

            alerts = (
                AlertService(get_settings())
                .compute_one(symbol, timeframe)
                .get("alerts", [])
            )
            candles = candle_store.load(symbol, timeframe)
            horizon = horizon_for(timeframe)
            from ..alerts.forward import ForwardReturnModel

            model = ForwardReturnModel(candles, horizon=horizon)
            stats = model.stats_by_band(alerts, field=_field("net", horizon))
            for band in sorted(stats):
                st = stats[band]
                pm, pw, n = posterior_stats(st)
                reason = gate_rejects(st)
                market["bands"].append(
                    {
                        "band": band,
                        "n_observed": n,
                        "cold_start": n < PRIOR_N,
                        "win_rate_raw": _fmt_price(st.win_rate),
                        "mean_return_raw": _fmt_price(st.mean_return),
                        "risk": _fmt_price(st.risk),
                        "posterior_win_rate": _fmt_price(pw),
                        "posterior_mean_return": _fmt_price(pm),
                        "gate": "PASS" if reason is None else reason,
                    }
                )
        except Exception as exc:  # noqa: BLE001 - one bad market must not kill the pack
            market["error"] = str(exc)
        out["markets"].append(market)
    return out


# --------------------------------------------------------------------------- #
# 5. Phase5 deep backtest summary (the statistically-sized sample).
# --------------------------------------------------------------------------- #


_PHASE5_FILES = [
    "phase5-BTC-USDT-4H.json",
    "phase5-ETH-USDT-4H.json",
    "phase5-BTC-USDT-1D.json",
    "phase5-ETH-USDT-1D.json",
]


def _phase5_summary() -> List[dict]:
    rows = []
    for name in _PHASE5_FILES:
        path = os.path.join("reports", "phase5", name)
        if not os.path.exists(path):
            rows.append({"file": name, "error": "missing"})
            continue
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        overview = data["validation"]["overall"]
        risk = overview["risk"]
        entry = {
            "symbol": data["validation"]["symbol"],
            "timeframe": data["validation"]["timeframe"],
            "candles": data["validation"]["candle_count"],
            "strategy": data["validation"]["strategy_version"],
            "verdict": data["card"].get("verdict"),
        }
        periods = []
        for p in data["validation"].get("periods") or []:
            m = p["metrics"]
            periods.append(
                {
                    "label": p["label"],
                    "trades": m["trades"]["completed_trades"],
                    "win_rate": _fmt_price(m["trades"]["win_rate"]),
                    "avg_return": _fmt_price(m["trades"]["average_return"]),
                    "expectancy": _fmt_price(m["risk"]["expectancy"]),
                    "max_drawdown": _fmt_price(m["risk"]["max_drawdown"]),
                    "longest_losing_streak": m["risk"]["longest_losing_streak"],
                    "profit_factor": _fmt_price(m["risk"]["profit_factor"]),
                    "avg_holding_bars": _fmt_price(m["risk"]["average_holding_bars"]),
                    "total_fees": _fmt_price(m["risk"]["total_fees"]),
                }
            )
        entry["full"] = {
            "trades": overview["trades"]["completed_trades"],
            "win_rate": _fmt_price(overview["trades"]["win_rate"]),
            "avg_return": _fmt_price(overview["trades"]["average_return"]),
            "expectancy": _fmt_price(overview["risk"]["expectancy"]),
            "max_drawdown": _fmt_price(overview["risk"]["max_drawdown"]),
            "longest_losing_streak": overview["risk"]["longest_losing_streak"],
            "profit_factor": _fmt_price(overview["risk"]["profit_factor"]),
            "total_fees": _fmt_price(overview["risk"]["total_fees"]),
            "setups_per_bar": _fmt_price(overview["opportunity"]["setups_per_bar"]),
        }
        entry["periods"] = periods
        rows.append(entry)
    return rows


# --------------------------------------------------------------------------- #
# 6. Cost tracker.
# --------------------------------------------------------------------------- #


def _cost_tracker(data_dir: str = "data/paper_live") -> Dict[str, Any]:
    path = os.path.join(data_dir, "cost_tracker.jsonl")
    records = []
    total_fees = 0.0
    total_slippage = 0.0
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                records.append(rec)
                total_fees += float(rec.get("fee", 0.0) or 0.0)
                total_slippage += float(rec.get("slippage", 0.0) or 0.0)
    return {
        "records": len(records),
        "total_fees_usd": _fmt_price(total_fees),
        "total_slippage_usd": _fmt_price(total_slippage),
        "entries": records[-200:],
    }


# --------------------------------------------------------------------------- #


def main() -> int:
    os.makedirs(OUTDIR, exist_ok=True)

    ledgers = _load_ledgers()
    trades = _settled_trades(ledgers)
    open_positions = _open_positions(ledgers)

    # 1. Trade log (the honest small sample).
    rows = [t.as_row() for t in trades]
    header = [
        "symbol", "timeframe", "side", "band", "entry_ts", "entry_price",
        "notional", "stop_price", "exit_ts", "exit_price", "exit_reason",
        "gross_return", "fees", "net_return", "pnl", "cold_start",
        "n_observed_at_decision_time",
    ]
    _write_csv(os.path.join(OUTDIR, "trades.csv"), header, rows)
    with open(os.path.join(OUTDIR, "trades.json"), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "note": "paper/simulation only — no real orders, no money",
                "trades": rows,
                "open_positions": open_positions,
            },
            fh, indent=2,
        )

    # 2. Equity curve for the realized sequence.
    equity: List[Dict[str, Any]] = []
    cum = _START_CASH
    for t in trades:
        cum += t.pnl or 0.0
        equity.append(
            {
                "exit_ts": t.exit_ts,
                "symbol": t.symbol,
                "timeframe": t.timeframe,
                "pnl": t.pnl,
                "cumulative_pnl": cum - _START_CASH if _START_CASH else None,
                "equity": _fmt_price(cum),
            }
        )
    _write_csv(
        os.path.join(OUTDIR, "equity_curve.csv"),
        ["exit_ts", "symbol", "timeframe", "pnl", "cumulative_pnl", "equity"],
        equity,
    )

    # 3. Drawdown (per market + aggregate).
    aggregate = _drawdown_metrics(trades)
    per_market = {}
    for t in trades:
        key = f"{t.symbol} {t.timeframe}"
        per_market.setdefault(key, []).append(t)
    drawdown = {"aggregate": aggregate}
    drawdown["per_market"] = {
        key: _drawdown_metrics(v) for key, v in sorted(per_market.items())
    }

    # 4. Band evidence.
    evidence = _band_evidence()

    # 5. Phase5 summary.
    phase5 = _phase5_summary()

    # 6. Cost tracker.
    costs = _cost_tracker()

    pack = {
        "generated_by": "veyra.research.proof_pack",
        "simulation_only": "There is no live trading. Every trade in this pack is a "
                           "paper simulation; no real orders, no money, no slippage to "
                           "measure beyond the cost model.",
        "trades": rows,
        "open_positions": len(open_positions),
        "equity_curve": equity,
        "drawdown": drawdown,
        "band_evidence": evidence,
        "phase5": phase5,
        "cost_tracker": costs,
    }
    with open(os.path.join(OUTDIR, "proof_pack.json"), "w", encoding="utf-8") as fh:
        json.dump(pack, fh, indent=2)

    # ---- console report ---- #
    print(f"Proof pack -> {OUTDIR}/")
    print(f"\n[1] Realized paper trades: {len(rows)}  (open positions: {len(open_positions)})")
    if rows:
        print(f"    aggregate: wins={aggregate['wins']} losses={aggregate['losses']} "
              f"wr={aggregate['win_rate']:.3f} sum_pnl=${aggregate['sum_pnl']}")
        print(f"    max_drawdown=${aggregate['max_drawdown_usd']} "
              f"longest_losing_streak={aggregate['longest_losing_streak']}")
    for t in trades:
        n = t.n_observed if t.n_observed is not None else "-"
        print(f"    {t.symbol} {t.timeframe} {t.side} {t.band}"
              f" entry@{t.entry_price} exit@{t.exit_price} ({t.exit_reason})"
              f" net={t.net_return if t.net_return is None else round(t.net_return, 4)}"
              f" pnl=${t.pnl or 0.0:+.2f}"
              f" n_observed={n} cold={t.cold_start}")

    print("\n[2] Band evidence (gate the paper engine applies):")
    for m in evidence["markets"]:
        active = [b for b in m["bands"] if b["n_observed"] > 0]
        if active:
            for b in active:
                print(f"    {m['symbol']} {m['timeframe']} {b['band']}:"
                      f" n={b['n_observed']} wr_raw={b['win_rate_raw']}"
                      f" wr_post={b['posterior_win_rate']}"
                      f" mean={b['mean_return_raw']} post={b['posterior_mean_return']}"
                      f" gate={b['gate']}")

    print("\n[3] Phase5 deep backtests (statistically-sized):")
    for p in phase5:
        if "error" in p:
            print(f"    {p['file']}: {p['error']}")
            continue
        oos = next((x for x in p["periods"] if x["label"] == "OOS"), None)
        oos_txt = (
            f"OOS: n={oos['trades']} wr={oos['win_rate']} avg={oos['avg_return']}"
            f" maxdd={oos['max_drawdown']} streak={oos['longest_losing_streak']}"
            if oos else "OOS: n/a"
        )
        print(f"    {p['symbol']} {p['timeframe']} ({p['candles']} candles)"
              f" verdict={p['verdict']} | full n={p['full']['trades']}"
              f" wr={p['full']['win_rate']} avg={p['full']['avg_return']} | {oos_txt}")

    print(f"\n[4] Cost tracker: {costs['records']} records,"
          f" fees=${costs['total_fees_usd']} slippage=${costs['total_slippage_usd']}")
    return 0


_START_CASH = 10_000.0


if __name__ == "__main__":
    raise SystemExit(main())