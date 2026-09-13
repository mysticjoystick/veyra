"""Experiment E3 — Entry Timing / Confirmation.

Phase 5.1 Research Experiment.

SINGLE CHANGE: Delay entry by exactly one additional CONFIRMED bar after a setup
qualifies. The engine already fills armqualified setups at the open of the bar
following qualification; E3 shifts that fill one bar later (the setup must still
be qualified after one more bar of `advance` before it may enter).

Everything else is untouched:
  - setup detection, targets, stops, scoring, confirmation rules,
  - position lifetime, overlap policy, fees/slippage, entry policy,
  - the frozen baseline engine itself.

LEAKAGE CONTROLS:
  - Chronological replay only. No future candles.
  - The delayed entry is filled at the open of a bar whose decision inputs are
    the candles up to and including the preceding bar (already known at that
    open). No information from the fill bar or later is used to decide entry.
  - The delay is a pure one-bar shift of the existing entry; it introduces no
    new decision input.
  - IN / VAL / OOS separation is preserved; split labels are purely descriptive
    here (there is nothing to tune — the single change is fixed).

Usage:
    python src/veyra/research/e3_entry_timing.py
    python src/veyra/research/e3_entry_timing.py --fast  # 1D only
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List

from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from veyra.backtest import MetricsEngine, SimulationResult
from veyra.backtest.metrics import MetricsResult
from veyra.backtest.models import BacktestTrade, SetupRecord
from veyra.backtest.simulator import Simulator
from veyra.config import get_settings
from veyra.data.candle_store import CandleStore
from veyra.phase5.frozen_config import build_baseline_engine
from veyra.phase5.validate import FIXED_SCORE_BUCKETS, chronological_split

# ── Experiment version ─────────────────────────────────────────────────────
E3_VERSION = "e3-entry-timing-v1"
BASELINE_VERSION = "phase5-baseline-v1"


# ── Split helpers ──────────────────────────────────────────────────────────

def _split_trades(
    trades: List[BacktestTrade],
    setups: List[SetupRecord],
    times,
) -> Dict[str, tuple]:
    """Return {split_label: (trades, setups)} sliced by chronological split."""
    split = chronological_split(len(times))

    def _ts_range(seg):
        return int(times[seg.start]), int(times[seg.end - 1])

    train_s, train_e = _ts_range(split.training)
    val_s, val_e = _ts_range(split.validation)
    oos_s, oos_e = _ts_range(split.test)

    def _filter_trades(lo, hi):
        return [t for t in trades if lo <= int(t.entry_ts) <= hi]

    def _filter_setups(lo, hi):
        return [s for s in setups if lo <= int(s.detection_ts) <= hi]

    return {
        "IN": (_filter_trades(train_s, train_e), _filter_setups(train_s, train_e)),
        "VAL": (_filter_trades(val_s, val_e), _filter_setups(val_s, val_e)),
        "OOS": (_filter_trades(oos_s, oos_e), _filter_setups(oos_s, oos_e)),
        "IN+VAL": (
            _filter_trades(train_s, val_e),
            _filter_setups(train_s, val_e),
        ),
    }


# ── Metrics helpers ────────────────────────────────────────────────────────

def _summarize(
    label: str,
    trades: List[BacktestTrade],
    setups: List[SetupRecord],
    me: MetricsEngine,
    total_candles: int,
) -> dict:
    m: MetricsResult = me.compute(
        trades, setups, total_candles, score_buckets=FIXED_SCORE_BUCKETS
    )
    total = len(trades)
    if total == 0:
        return {"label": label, "trades": 0}

    stops = sum(1 for t in trades if t.exit_reason == "STOP")
    targets = sum(1 for t in trades if t.exit_reason == "TARGET")
    expired = sum(1 for t in trades if t.exit_reason == "EXPIRED")
    avg_mae = sum(t.max_adverse_excursion for t in trades) / total
    avg_mfe = sum(t.max_favorable_excursion for t in trades) / total
    mae_mfe_ratio = avg_mae / avg_mfe if avg_mfe else None

    return {
        "label": label,
        "trades": total,
        "setups": len(setups),
        "win_rate": m.trades.win_rate * 100,
        "profit_factor": m.risk.profit_factor,
        "expectancy": m.risk.expectancy,
        "avg_return": sum(t.net_return for t in trades) / total,
        "avg_holding": m.risk.average_holding_bars,
        "stop_rate": stops / total * 100,
        "target_rate": targets / total * 100,
        "expired_rate": expired / total * 100,
        "max_drawdown": m.risk.max_drawdown,
        "avg_mae": avg_mae,
        "avg_mfe": avg_mfe,
        "mae_mfe_ratio": mae_mfe_ratio,
        "cum_return": m.risk.cumulative_return,
        "opportunity_expiry": m.opportunity.expiry_rate * 100,
        "opportunity_trigger": m.opportunity.trigger_rate * 100,
        "opportunity_qualification": m.opportunity.qualification_rate * 100,
        "score_buckets": {
            k: v.to_dict() for k, v in m.by_score_bucket.items()
        },
    }


def _regime_breakdown(trades: List[BacktestTrade]) -> Dict[str, dict]:
    by_regime: Dict[str, List[BacktestTrade]] = {}
    for t in trades:
        by_regime.setdefault(t.regime, []).append(t)
    out = {}
    for regime, ts in sorted(by_regime.items()):
        n = len(ts)
        rets = [t.net_return for t in ts]
        gw = sum(r for r in rets if r > 0)
        gl = abs(sum(r for r in rets if r < 0))
        pf = gw / gl if gl > 0 else (float("inf") if gw > 0 else None)
        stops = sum(1 for t in ts if t.exit_reason == "STOP")
        targets = sum(1 for t in ts if t.exit_reason == "TARGET")
        expired = sum(1 for t in ts if t.exit_reason == "EXPIRED")
        avg_mae = sum(t.max_adverse_excursion for t in ts) / n
        avg_mfe = sum(t.max_favorable_excursion for t in ts) / n
        out[regime] = {
            "n": n,
            "expectancy": sum(rets) / n if n else 0.0,
            "pf": pf,
            "win_rate": sum(1 for r in rets if r > 0) / n if n else 0.0,
            "exp_rate": expired / n * 100 if n else 0.0,
            "stop_rate": stops / n * 100 if n else 0.0,
            "tgt_rate": targets / n * 100 if n else 0.0,
            "avg_mae": avg_mae,
            "avg_mfe": avg_mfe,
        }
    return out


def _setup_family_breakdown(trades: List[BacktestTrade]) -> Dict[str, dict]:
    by_type: Dict[str, List[BacktestTrade]] = {}
    for t in trades:
        by_type.setdefault(t.setup_type, []).append(t)
    out = {}
    for st, ts in sorted(by_type.items()):
        n = len(ts)
        rets = [t.net_return for t in ts]
        gw = sum(r for r in rets if r > 0)
        gl = abs(sum(r for r in rets if r < 0))
        pf = gw / gl if gl > 0 else (float("inf") if gw > 0 else None)
        out[st] = {
            "n": n,
            "expectancy": sum(rets) / n if n else 0.0,
            "pf": pf,
            "win_rate": sum(1 for r in rets if r > 0) / n if n else 0.0,
        }
    return out


# ── Print helpers ──────────────────────────────────────────────────────────

def _fmt_pf(pf):
    if pf is None:
        return "  N/A"
    if pf == float("inf"):
        return "  inf"
    return f"{pf:5.2f}"


def _fmt_ratio(r):
    if r is None:
        return "  N/A"
    return f"{r:6.3f}"


def _print_summary_table(rows: List[dict]) -> None:
    header = (
        f"{'Label':<14} | {'Trades':>6} | {'Exp%':>6} | {'Stop%':>6} | {'Tgt%':>6} | "
        f"{'Expect':>8} | {'AvgRet':>8} | {'PF':>5} | {'Win%':>6} | {'Hold':>5} | "
        f"{'AvgMAE':>8} | {'AvgMFE':>8} | {'MAE/MFE':>6} | {'DD':>8}"
    )
    sep = "-" * len(header)
    print(header)
    print(sep)
    for m in rows:
        if not m or m.get("trades", 0) == 0:
            print(f"{m.get('label', ''):<14} | (no trades)")
            continue
        pf_s = _fmt_pf(m.get("profit_factor"))
        print(
            f"{m['label']:<14} | {m['trades']:>6} | {m['expired_rate']:>5.1f}% | "
            f"{m['stop_rate']:>5.1f}% | {m['target_rate']:>5.1f}% | "
            f"{m['expectancy']:>8.4f} | {m['avg_return']:>8.4f} | {pf_s} | "
            f"{m['win_rate']:>5.1f}% | {m['avg_holding']:>5.1f} | "
            f"{m['avg_mae']:>8.2f} | {m['avg_mfe']:>8.2f} | "
            f"{_fmt_ratio(m['mae_mfe_ratio']):>6} | {m['max_drawdown']:>8.4f}"
        )


# ── Core experiment ────────────────────────────────────────────────────────

def run_e3(symbol: str, timeframe: str) -> dict:
    settings = get_settings()
    store = CandleStore(settings)
    df = store.load(symbol, timeframe)
    df = df.sort_values("open_time").reset_index(drop=True)
    me = MetricsEngine()
    times = df["open_time"].to_numpy()
    n_candles = len(df)

    print(f"\n{'='*70}")
    print(f"E3 — {symbol} {timeframe}")
    print(f"{'='*70}")

    # ── STEP 1: Run frozen baseline once ──────────────────────────────────
    baseline_engine = build_baseline_engine(settings, progressive=True)
    baseline_run = baseline_engine.run(
        symbol, timeframe, df.copy(), run_key=f"e3-baseline|{symbol}|{timeframe}"
    )
    splits = _split_trades(baseline_run.trades, baseline_run.setups, times)
    in_trades, in_setups = splits["IN"]
    val_trades, val_setups = splits["VAL"]
    oos_trades, oos_setups = splits["OOS"]
    in_val_trades = in_trades + val_trades
    in_val_setups = in_setups + val_setups

    # ── STEP 2: Run E3 (one-bar entry delay via enter() intercept) ────────
    #
    # Entry path (frozen engine loop, next_open execution):
    #   1. Setup engine DETECTs a setup (pipeline analyse up to bar i).
    #   2. advance() promotes DETECTED -> DEVELOPING -> QUALIFIED when momentum
    #      continues to support the side.
    #   3. At step 7 of the qualifying bar the engine arms the QUALIFIED setup
    #      (active_qualifying()) into pending_entries.
    #   4. Next bar open, step 1 fills pending entries via Simulator.enter().
    #
    # So a setup that qualifies on bar X is filled at bar X+1 open. E3 defers
    # the FIRST enter() attempt per setup: the fill is skipped, the setup stays
    # QUALIFIED, and the engine re-arms it (still qualified) so the fill occurs
    # at bar X+2 open — one additional CONFIRMED bar (bar X+1) after which the
    # setup must still be qualified. If bar X+1's advance() expires/invalidates
    # it, active_qualifying() no longer returns it and it never enters.
    #
    # Look-ahead: the fill bar's open is decided using only candles up to the
    # previous close (already known). The confirmation bar X+1 is fully known at
    # bar X+2's open. No future candles inform the decision.

    deferral_count: Dict[str, int] = {}
    delayed_setups: set = set()

    orig_enter = Simulator.enter

    def patched_enter(self, key, raw_open, ts, bar_index, normalizer):
        # Defer the FIRST entry attempt for each setup; proceed on the second
        # (one additional confirmed bar). Deterministic per setup key.
        attempt = deferral_count.get(key, 0)
        if attempt == 0:
            deferral_count[key] = 1
            return None  # defer: setup remains QUALIFIED, re-armed next bar
        return orig_enter(self, key, raw_open, ts, bar_index, normalizer)

    e3_engine = build_baseline_engine(settings, progressive=True)
    with patch.object(Simulator, "enter", new=patched_enter):
        e3_run = e3_engine.run(
            symbol, timeframe, df.copy(),
            run_key=f"e3-delayed|{symbol}|{timeframe}"
        )

    e3_splits = _split_trades(e3_run.trades, e3_run.setups, times)
    e3_in_trades, e3_in_setups = e3_splits["IN"]
    e3_val_trades, e3_val_setups = e3_splits["VAL"]
    e3_oos_trades, e3_oos_setups = e3_splits["OOS"]
    e3_in_val_trades = e3_in_trades + e3_val_trades
    e3_in_val_setups = e3_in_setups + e3_val_setups

    # Setups that were first-filtered into a delay (qualified setups that got an
    # entry attempt deferred). These are the NOTional "delayed" opportunities.
    delayed_count = len(deferral_count)

    # ── STEP 3: Print comparison tables ───────────────────────────────────
    def _print_section(title):
        print(f"\n[{title}]")
        print("  Baseline fills at the open of the bar following qualification.")
        print("  E3-Delayed fills one bar later (requires a second confirmation).")

    _print_section("IN COMPARISON")
    _print_summary_table([
        _summarize("Baseline", in_trades, in_setups, me, n_candles),
        _summarize("E3-Delayed", e3_in_trades, e3_in_setups, me, n_candles),
    ])

    _print_section("VAL COMPARISON — PRIMARY DECISION WINDOW")
    _print_summary_table([
        _summarize("Baseline", val_trades, val_setups, me, n_candles),
        _summarize("E3-Delayed", e3_val_trades, e3_val_setups, me, n_candles),
    ])

    print("\n[IN+VAL COMBINED]")
    _print_summary_table([
        _summarize("Baseline", in_val_trades, in_val_setups, me, n_candles),
        _summarize("E3-Delayed", e3_in_val_trades, e3_in_val_setups, me, n_candles),
    ])

    print("\n[OOS — reference only, NOT used for any selection/tuning]")
    _print_summary_table([
        _summarize("Baseline", oos_trades, oos_setups, me, n_candles),
        _summarize("E3-Delayed", e3_oos_trades, e3_oos_setups, me, n_candles),
    ])

    # ── STEP 4: Regime breakdown (Baseline vs E3, IN+VAL) ────────────────
    print("\n[REGIME BREAKDOWN — Baseline IN+VAL]")
    reg_bl = _regime_breakdown(in_val_trades)
    print(
        f"{'Regime':<20} | {'n':>5} | {'Expect':>8} | {'PF':>5} | {'Win%':>6} | "
        f"{'Exp%':>6} | {'Stop%':>6} | {'Tgt%':>6} | {'MAE':>8} | {'MFE':>8}"
    )
    print("-" * 105)
    for regime, v in reg_bl.items():
        print(
            f"{regime:<20} | {v['n']:>5} | {v['expectancy']:>8.4f} | {_fmt_pf(v['pf'])} | "
            f"{v['win_rate']*100:>5.1f}% | {v['exp_rate']:>5.1f}% | {v['stop_rate']:>5.1f}% | "
            f"{v['tgt_rate']:>5.1f}% | {v['avg_mae']:>8.2f} | {v['avg_mfe']:>8.2f}"
        )

    print("\n[REGIME BREAKDOWN — E3-Delayed IN+VAL]")
    reg_e3 = _regime_breakdown(e3_in_val_trades)
    for regime, v in reg_e3.items():
        print(
            f"{regime:<20} | {v['n']:>5} | {v['expectancy']:>8.4f} | {_fmt_pf(v['pf'])} | "
            f"{v['win_rate']*100:>5.1f}% | {v['exp_rate']:>5.1f}% | {v['stop_rate']:>5.1f}% | "
            f"{v['tgt_rate']:>5.1f}% | {v['avg_mae']:>8.2f} | {v['avg_mfe']:>8.2f}"
        )

    # ── STEP 5: Setup family breakdown (Baseline vs E3, IN+VAL) ──────────
    print("\n[SETUP FAMILY — Baseline IN+VAL]")
    sf_bl = _setup_family_breakdown(in_val_trades)
    print(f"{'Family':<22} | {'n':>5} | {'Expect':>8} | {'PF':>5} | {'Win%':>6}")
    print("-" * 60)
    for st, v in sf_bl.items():
        print(
            f"{st:<22} | {v['n']:>5} | {v['expectancy']:>8.4f} | "
            f"{_fmt_pf(v['pf'])} | {v['win_rate']*100:>5.1f}%"
        )

    print("\n[SETUP FAMILY — E3-Delayed IN+VAL]")
    sf_e3 = _setup_family_breakdown(e3_in_val_trades)
    for st, v in sf_e3.items():
        print(
            f"{st:<22} | {v['n']:>5} | {v['expectancy']:>8.4f} | "
            f"{_fmt_pf(v['pf'])} | {v['win_rate']*100:>5.1f}%"
        )

    # ── STEP 6: Score-bucket results (IN+VAL) ─────────────────────────────
    bl_score = _summarize("BL", in_val_trades, in_val_setups, me, n_candles)["score_buckets"]
    e3_score = _summarize("E3", e3_in_val_trades, e3_in_val_setups, me, n_candles)["score_buckets"]
    print("\n[SCORE BUCKETS — Baseline IN+VAL]")
    print(f"{'Bucket':<10} | {'n':>5} | {'Exp':>8} | {'PF':>5} | {'Win%':>6}")
    print("-" * 48)
    for k, v in sorted(bl_score.items()):
        print(
            f"{k:<10} | {v['trades']:>5} | {v['expectancy']:>8.4f} | "
            f"{_fmt_pf(v['profit_factor'])} | {v['win_rate']*100:>5.1f}%"
        )
    print("\n[SCORE BUCKETS — E3-Delayed IN+VAL]")
    for k, v in sorted(e3_score.items()):
        print(
            f"{k:<10} | {v['trades']:>5} | {v['expectancy']:>8.4f} | "
            f"{_fmt_pf(v['profit_factor'])} | {v['win_rate']*100:>5.1f}%"
        )

    # ── STEP 7: Opportunity / delay accounting ───────────────────────────
    bl_in_val_trades_n = len(in_val_trades)
    e3_in_val_trades_n = len(e3_in_val_trades)
    print("\n[DELAY / OPPORTUNITY ACCOUNTING]")
    print(f"  QUALIFIED setups delayed (first enter() deferred): {delayed_count}")
    print(f"  IN+VAL trades baseline: {bl_in_val_trades_n} -> E3: {e3_in_val_trades_n} "
          f"(delta {e3_in_val_trades_n - bl_in_val_trades_n:+d})")

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "baseline_in": _summarize("BL-IN", in_trades, in_setups, me, n_candles),
        "e3_in": _summarize("E3-IN", e3_in_trades, e3_in_setups, me, n_candles),
        "baseline_val": _summarize("BL-VAL", val_trades, val_setups, me, n_candles),
        "e3_val": _summarize("E3-VAL", e3_val_trades, e3_val_setups, me, n_candles),
        "baseline_oos": _summarize("BL-OOS", oos_trades, oos_setups, me, n_candles),
        "e3_oos": _summarize("E3-OOS", e3_oos_trades, e3_oos_setups, me, n_candles),
        "baseline_inval": _summarize("BL-INVAL", in_val_trades, in_val_setups, me, n_candles),
        "e3_inval": _summarize("E3-INVAL", e3_in_val_trades, e3_in_val_setups, me, n_candles),
        "delayed_setups": delayed_count,
    }


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="E3 Entry Timing experiment")
    parser.add_argument("--fast", action="store_true", help="Run 1D only")
    args = parser.parse_args()

    datasets = [("BTC/USDT", "1D"), ("ETH/USDT", "1D")]
    if not args.fast:
        datasets += [("BTC/USDT", "4H"), ("ETH/USDT", "4H")]

    all_results = []
    for sym, tf in datasets:
        r = run_e3(sym, tf)
        all_results.append(r)

    print("\n\n" + "=" * 70)
    print("E3 CROSS-DATASET SUMMARY")
    print("=" * 70)
    metrics = [
        "expectancy", "profit_factor", "win_rate", "expired_rate",
        "avg_mae", "avg_mfe", "mae_mfe_ratio", "max_drawdown", "avg_holding",
    ]
    for r in all_results:
        label = f"{r['symbol']} {r['timeframe']}"
        bl_val = r["baseline_val"]
        e3_val = r["e3_val"]
        print(f"\n{label}  (VAL window — primary decision)")
        print(f"{'Metric':<14} | {'Baseline':>12} | {'E3-Delayed':>12} | {'Delta':>10}")
        print("-" * 56)
        if bl_val.get("trades", 0) == 0:
            print("  (no VAL trades)")
            continue
        for metric in metrics:
            bl_v = bl_val.get(metric, 0) or 0
            e3_v = e3_val.get(metric, 0) or 0
            if isinstance(bl_v, float) and isinstance(e3_v, float) and metric != "profit_factor":
                delta = e3_v - bl_v
                print(f"{metric:<14} | {bl_v:>12.4f} | {e3_v:>12.4f} | {delta:>+10.4f}")
            else:
                print(f"{metric:<14} | {bl_v:>12.4f} | {e3_v:>12.4f}")
        print(f"{'trades':<14} | {bl_val.get('trades',0):>12} | {e3_val.get('trades',0):>12} | "
              f"{e3_val.get('trades',0)-bl_val.get('trades',0):>+10d}")