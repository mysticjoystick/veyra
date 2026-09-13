"""Experiment E2 — Regime Entry Filter.

Phase 5.1 Research Experiment.

SINGLE CHANGE: Block entries in regimes whose expectancy on the IN split is
negative. Setup detection, targets, stops, scoring, and confirmation rules are
untouched. The baseline engine is never modified.

LEAKAGE CONTROLS:
  - Regime classification table is derived exclusively from the IN-split trades
    of each individual dataset.
  - The filter decision (ALLOW / BLOCK) is frozen before VAL or OOS results
    are inspected.
  - The filter uses the regime label already stamped on the setup at detection
    time (computed from past candles only) — no future information.
  - One filter per dataset (not pooled across assets/timeframes).

Usage:
    python src/veyra/research/e2_regime_filter.py
    python src/veyra/research/e2_regime_filter.py --fast  # 1D only
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional, Set
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
E2_VERSION = "e2-regime-filter-v1"
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


# ── Regime classification (IN-only) ───────────────────────────────────────

def _regime_expectancy_from_in(in_trades: List[BacktestTrade]) -> Dict[str, float]:
    """Return {regime: expectancy} computed exclusively from IN-split trades."""
    by_regime: Dict[str, List[float]] = {}
    for t in in_trades:
        by_regime.setdefault(t.regime, []).append(t.net_return)
    return {
        regime: (sum(rets) / len(rets) if rets else 0.0)
        for regime, rets in by_regime.items()
    }


def _regime_pf_from_in(in_trades: List[BacktestTrade]) -> Dict[str, Optional[float]]:
    """Return {regime: profit_factor} computed exclusively from IN-split trades."""
    by_regime: Dict[str, List[float]] = {}
    for t in in_trades:
        by_regime.setdefault(t.regime, []).append(t.net_return)
    result = {}
    for regime, rets in by_regime.items():
        gw = sum(r for r in rets if r > 0)
        gl = abs(sum(r for r in rets if r < 0))
        if gl > 0:
            result[regime] = gw / gl
        elif gw > 0:
            result[regime] = float("inf")
        else:
            result[regime] = None
    return result


def _build_filter(in_trades: List[BacktestTrade]) -> Set[str]:
    """Return the set of regimes to ALLOW (positive IN expectancy only).

    Filter rule (deterministic, frozen before VAL/OOS):
      ALLOW if IN expectancy > 0 AND n >= 30
      BLOCK otherwise (insufficient data or negative expectancy)
    """
    by_regime: Dict[str, List[float]] = {}
    for t in in_trades:
        by_regime.setdefault(t.regime, []).append(t.net_return)

    allowed: Set[str] = set()
    for regime, rets in by_regime.items():
        n = len(rets)
        exp = sum(rets) / n if n else 0.0
        if exp > 0 and n >= 30:
            allowed.add(regime)
    return allowed


# ── Metrics helpers ────────────────────────────────────────────────────────

def _summarize(
    label: str,
    trades: List[BacktestTrade],
    setups: List[SetupRecord],
    me: MetricsEngine,
    total_candles: int,
) -> dict:
    m: MetricsResult = me.compute(trades, setups, total_candles, score_buckets=FIXED_SCORE_BUCKETS)
    total = len(trades)
    if total == 0:
        return {"label": label, "trades": 0}

    stops = sum(1 for t in trades if t.exit_reason == "STOP")
    targets = sum(1 for t in trades if t.exit_reason == "TARGET")
    expired = sum(1 for t in trades if t.exit_reason == "EXPIRED")
    avg_mae = sum(t.max_adverse_excursion for t in trades) / total
    avg_mfe = sum(t.max_favorable_excursion for t in trades) / total

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
        "cum_return": m.risk.cumulative_return,
    }


def _regime_breakdown(trades: List[BacktestTrade], me: MetricsEngine) -> Dict[str, dict]:
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
        out[regime] = {
            "n": n,
            "expectancy": sum(rets) / n if n else 0.0,
            "pf": pf,
            "win_rate": sum(1 for r in rets if r > 0) / n if n else 0.0,
            "exp_rate": expired / n * 100 if n else 0.0,
            "stop_rate": stops / n * 100 if n else 0.0,
            "tgt_rate": targets / n * 100 if n else 0.0,
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


def _print_summary_table(rows: List[dict]) -> None:
    header = (
        f"{'Label':<14} | {'Trades':>6} | {'Exp%':>6} | {'Stop%':>6} | {'Tgt%':>6} | "
        f"{'Expect':>8} | {'AvgRet':>8} | {'PF':>5} | {'Win%':>6} | {'Hold':>5} | "
        f"{'AvgMAE':>8} | {'AvgMFE':>8} | {'Drawdown':>9}"
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
            f"{m['avg_mae']:>8.2f} | {m['avg_mfe']:>8.2f} | {m['max_drawdown']:>9.4f}"
        )


# ── Core experiment ────────────────────────────────────────────────────────

def run_e2(symbol: str, timeframe: str) -> dict:
    settings = get_settings()
    store = CandleStore(settings)
    df = store.load(symbol, timeframe)
    df = df.sort_values("open_time").reset_index(drop=True)
    me = MetricsEngine()
    times = df["open_time"].to_numpy()
    n_candles = len(df)

    print(f"\n{'='*70}")
    print(f"E2 — {symbol} {timeframe}")
    print(f"{'='*70}")

    # ── STEP 1: Run frozen baseline once ──────────────────────────────────
    baseline_engine = build_baseline_engine(settings, progressive=True)
    baseline_run = baseline_engine.run(
        symbol, timeframe, df.copy(), run_key=f"e2-baseline|{symbol}|{timeframe}"
    )
    splits = _split_trades(baseline_run.trades, baseline_run.setups, times)

    in_trades, in_setups = splits["IN"]
    val_trades, val_setups = splits["VAL"]
    oos_trades, oos_setups = splits["OOS"]
    in_val_trades = in_trades + val_trades
    in_val_setups = in_setups + val_setups

    # ── STEP 2: Derive IN-only regime classification ───────────────────────
    in_exp = _regime_expectancy_from_in(in_trades)
    in_pf = _regime_pf_from_in(in_trades)
    in_counts: Dict[str, int] = {}
    for t in in_trades:
        in_counts[t.regime] = in_counts.get(t.regime, 0) + 1

    # Build filter (frozen, must not use VAL/OOS)
    allowed_regimes = _build_filter(in_trades)

    # ── STEP 3: Print IN-only regime classification table ─────────────────
    all_regimes = sorted(set(t.regime for t in baseline_run.trades))
    print("\n[IN-ONLY REGIME CLASSIFICATION — filter frozen here]")
    print(f"{'Regime':<20} | {'IN n':>6} | {'IN Expect':>10} | {'IN PF':>8} | Decision")
    print("-" * 65)
    for reg in all_regimes:
        n = in_counts.get(reg, 0)
        exp = in_exp.get(reg, float("nan"))
        pf_v = in_pf.get(reg, None)
        decision = "ALLOW" if reg in allowed_regimes else "BLOCK"
        reason = "" if n >= 30 else " (n<30)"
        print(
            f"{reg:<20} | {n:>6} | {exp:>10.4f} | {_fmt_pf(pf_v)} | {decision}{reason}"
        )
    print(f"\nAllowed regimes: {sorted(allowed_regimes)}")
    print(f"Blocked regimes: {sorted(set(all_regimes) - allowed_regimes)}")

    # ── STEP 4: Run E2 with regime filter applied ─────────────────────────
    # The filter intercepts Simulator.enter() via patch. We need to know the
    # regime of the CURRENT setup that is about to be entered; it is stored on
    # the tracked Setup domain object (keyed in Simulator._tracked). Regimes are
    # never gated in can_enter() — the overlap policy there is left unchanged so
    # blocking an entry does not disturb the original overlap behaviour.

    orig_enter = Simulator.enter

    def patched_enter(self, key, raw_open, ts, bar_index, normalizer):
        # Get the tracked setup's regime and apply the filter.
        tracked = self._tracked.get(key)
        if tracked is not None:
            setup_regime = tracked.setup.regime.value
            if setup_regime not in allowed_regimes:
                # Block this entry — do not open a position.
                return None
        return orig_enter(self, key, raw_open, ts, bar_index, normalizer)

    e2_engine = build_baseline_engine(settings, progressive=True)
    with patch.object(Simulator, "enter", new=patched_enter):
        e2_run = e2_engine.run(
            symbol, timeframe, df.copy(),
            run_key=f"e2-filtered|{symbol}|{timeframe}"
        )

    e2_splits = _split_trades(e2_run.trades, e2_run.setups, times)
    e2_in_trades, e2_in_setups = e2_splits["IN"]
    e2_val_trades, e2_val_setups = e2_splits["VAL"]
    e2_oos_trades, e2_oos_setups = e2_splits["OOS"]
    e2_in_val_trades = e2_in_trades + e2_val_trades
    e2_in_val_setups = e2_in_setups + e2_val_setups

    # ── Trades blocked by regime filter ──────────────────────────────────
    blocked_in = len(in_trades) - len(e2_in_trades)
    blocked_val = len(val_trades) - len(e2_val_trades)
    blocked_oos = len(oos_trades) - len(e2_oos_trades)

    # ── STEP 5: Print comparison tables ──────────────────────────────────

    print("\n[IN COMPARISON]")
    _print_summary_table([
        _summarize("Baseline", in_trades, in_setups, me, n_candles),
        _summarize("E2-Filter", e2_in_trades, e2_in_setups, me, n_candles),
    ])

    print(f"\n  Blocked in IN: {blocked_in} trades")

    print("\n[VAL COMPARISON]  ← Primary decision window")
    _print_summary_table([
        _summarize("Baseline", val_trades, val_setups, me, n_candles),
        _summarize("E2-Filter", e2_val_trades, e2_val_setups, me, n_candles),
    ])
    print(f"\n  Blocked in VAL: {blocked_val} trades")

    print("\n[IN+VAL COMBINED]")
    _print_summary_table([
        _summarize("Baseline", in_val_trades, in_val_setups, me, n_candles),
        _summarize("E2-Filter", e2_in_val_trades, e2_in_val_setups, me, n_candles),
    ])

    print("\n[OOS — reference only, NOT used for filter selection or tuning]")
    _print_summary_table([
        _summarize("Baseline", oos_trades, oos_setups, me, n_candles),
        _summarize("E2-Filter", e2_oos_trades, e2_oos_setups, me, n_candles),
    ])
    print(f"\n  Blocked in OOS: {blocked_oos} trades")

    # ── STEP 6: Regime breakdown (IN+VAL) ────────────────────────────────
    print("\n[REGIME BREAKDOWN — Baseline IN+VAL]")
    reg_bl = _regime_breakdown(in_val_trades, me)
    print(f"{'Regime':<20} | {'n':>5} | {'Expect':>8} | {'PF':>5} | {'Win%':>6} | {'Exp%':>6} | {'Stop%':>6} | {'Tgt%':>6}")
    print("-" * 85)
    for regime, stats in reg_bl.items():
        print(
            f"{regime:<20} | {stats['n']:>5} | {stats['expectancy']:>8.4f} | "
            f"{_fmt_pf(stats['pf'])} | {stats['win_rate']*100:>5.1f}% | "
            f"{stats['exp_rate']:>5.1f}% | {stats['stop_rate']:>5.1f}% | {stats['tgt_rate']:>5.1f}%"
        )

    # ── STEP 7: Setup family breakdown (Baseline vs E2 IN+VAL) ───────────
    print("\n[SETUP FAMILY — Baseline IN+VAL]")
    sf_bl = _setup_family_breakdown(in_val_trades)
    print(f"{'Family':<22} | {'n':>5} | {'Expect':>8} | {'PF':>5} | {'Win%':>6}")
    print("-" * 60)
    for st, stats in sf_bl.items():
        print(f"{st:<22} | {stats['n']:>5} | {stats['expectancy']:>8.4f} | {_fmt_pf(stats['pf'])} | {stats['win_rate']*100:>5.1f}%")

    print("\n[SETUP FAMILY — E2-Filter IN+VAL]")
    sf_e2 = _setup_family_breakdown(e2_in_val_trades)
    print(f"{'Family':<22} | {'n':>5} | {'Expect':>8} | {'PF':>5} | {'Win%':>6}")
    print("-" * 60)
    for st, stats in sf_e2.items():
        print(f"{st:<22} | {stats['n']:>5} | {stats['expectancy']:>8.4f} | {_fmt_pf(stats['pf'])} | {stats['win_rate']*100:>5.1f}%")

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "allowed_regimes": sorted(allowed_regimes),
        "blocked_regimes": sorted(set(all_regimes) - allowed_regimes),
        "in_regime_exp": in_exp,
        "in_regime_pf": in_pf,
        "in_regime_n": in_counts,
        "baseline_in": _summarize("BL-IN", in_trades, in_setups, me, n_candles),
        "e2_in": _summarize("E2-IN", e2_in_trades, e2_in_setups, me, n_candles),
        "baseline_val": _summarize("BL-VAL", val_trades, val_setups, me, n_candles),
        "e2_val": _summarize("E2-VAL", e2_val_trades, e2_val_setups, me, n_candles),
        "baseline_oos": _summarize("BL-OOS", oos_trades, oos_setups, me, n_candles),
        "e2_oos": _summarize("E2-OOS", e2_oos_trades, e2_oos_setups, me, n_candles),
        "blocked_in": blocked_in,
        "blocked_val": blocked_val,
        "blocked_oos": blocked_oos,
    }


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="E2 Regime Filter experiment")
    parser.add_argument("--fast", action="store_true", help="Run 1D only")
    args = parser.parse_args()

    datasets = [("BTC/USDT", "1D"), ("ETH/USDT", "1D")]
    if not args.fast:
        datasets += [("BTC/USDT", "4H"), ("ETH/USDT", "4H")]

    all_results = []
    for sym, tf in datasets:
        r = run_e2(sym, tf)
        all_results.append(r)

    print("\n\n" + "=" * 70)
    print("E2 CROSS-DATASET SUMMARY")
    print("=" * 70)
    print(f"\n{'Dataset':<20} | {'Metric':<12} | {'Baseline':>10} | {'E2-Filter':>10} | {'Delta':>8}")
    print("-" * 75)
    for r in all_results:
        label = f"{r['symbol']} {r['timeframe']}"
        bl_val = r["baseline_val"]
        e2_val = r["e2_val"]
        if bl_val.get("trades", 0) == 0:
            continue
        for metric in ["expectancy", "profit_factor", "win_rate", "expired_rate"]:
            bl_v = bl_val.get(metric, 0) or 0
            e2_v = e2_val.get(metric, 0) or 0
            delta = e2_v - bl_v
            print(f"{label:<20} | {metric:<12} | {bl_v:>10.4f} | {e2_v:>10.4f} | {delta:>+8.4f}")
        print()
