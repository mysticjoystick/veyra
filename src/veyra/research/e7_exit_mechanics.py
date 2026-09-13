"""E7 — Exit-Mechanics Attribution (diagnostic only).

Central question:
    Are Veyra's poor trade outcomes primarily caused by exit/position mechanics
    rather than the detection signal, and if so, which mechanic is responsible?

MUST-NOT (enforced):
  * No change to production, frozen baseline, strategy params, detectors, scoring,
    qualification, entry timing, stops, targets, or timeout settings.
  * No beating-out-of-sample / threshold tuning / VAL-OOS optimisation.
  * No *re-simulation* of the strategy under alternative exits. Counterfactuals
    are DESCRIPTIVE labels read from the already-recorded raw price path after a
    trade's realized exit; they never feed a decision.

Design (fully causal for features; labels are realized-path):
  1. Run the frozen baseline ONCE per dataset (progressive, phase5-baseline-v1).
     Reuse run.trades (realized outcomes) + run.setups.
  2. Run an independent frozen DETECTION pass to recover each setup's
     interest_area + targets (needed for stop/target geometry, which BacktestTrade
     does not persist). Join deterministically on (setup_type, detection_ts).
  3. Map each trade to its bar range via entry_ts -> df index (validated: holding
     = exit_idx - entry_idx, 0 mismatches). Walk df OHLC to compute:
       - stop/target geometry (entry->stop, entry->target, risk:reward)
       - bars_to_MFE / bars_to_MAE (timing of extremes within the holding)
       - return at fractions of holding (10/25/50/75/100% of held bars)
       - MAE/MFE timing: favourable-first vs against-first
       - counterfactual: did the path AFTER exit later reach target/stop; max
         future excursion within +60 bars (descriptive only)
       - exit-reason profile classification (STOP/TARGET/EXPIRED)
  Outputs are written to reports/phase5_1/_e7_data.json.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from veyra.config import get_settings
from veyra.data.candle_store import CandleStore
from veyra.market.pipeline import MarketAnalysisPipeline
from veyra.phase5.frozen_config import build_baseline_engine, PHASE5_BASELINE_VERSION
from veyra.strategy.setup_engine import SetupEngine

COUNTERFACTUAL_BARS = 60   # descriptive look-ahead window after realized exit
FRACTIONS = [0.10, 0.25, 0.50, 0.75, 1.00]
ENTRY_FEE = 0.001
EXIT_FEE = 0.001


def baseline_run(settings, symbol: str, timeframe: str, df: pd.DataFrame):
    engine = build_baseline_engine(settings, strategy_version=PHASE5_BASELINE_VERSION, progressive=True)
    return engine.run(symbol, timeframe, df.copy(), run_key=f"e7|{symbol}|{timeframe}")


def detection_geometry(settings, symbol: str, timeframe: str, df: pd.DataFrame) -> Dict:
    """Recover per-detection setup geometry (interest_area + targets) keyed by
    (setup_type, detection_ts), using the SAME frozen SetupEngine the baseline
    uses so the join is exact."""
    pipeline = MarketAnalysisPipeline.default(settings)
    snapshots = pipeline.analyze_each(symbol, timeframe, df)
    setup_engine = SetupEngine(settings)
    warmup = max((e.warmup_required() for e in pipeline._engines.values()), default=0)
    out = {}
    for i in range(warmup, len(df)):
        for d in setup_engine.detect(snapshots[i]):
            ia = d.interest_area
            tgt = d.targets[0] if d.targets else None
            out[(d.setup_type.value, d.timestamp)] = {
                "ia_low": ia.low if ia else None,
                "ia_high": ia.high if ia else None,
                "target_low": tgt.low if tgt else None,
                "target_high": tgt.high if tgt else None,
                "side": d.side.value,
                "score": d.overall_score,
            }
    return out


def _net_at_close(close: float, entry_price: float, side: str) -> float:
    sign = 1.0 if side == "LONG" else -1.0
    gross = (close - entry_price) / entry_price * sign
    return gross - (ENTRY_FEE + EXIT_FEE)


def trade_forensics(sym, tf, t, df, ts_to_idx, interval, sd, n_candles) -> dict:
    ei = ts_to_idx.get(t.entry_ts)
    xi = ts_to_idx.get(t.exit_ts)
    side = t.side
    ref = float(df["open"].iloc[ei]) if ei is not None else None
    train_end = int(round(n_candles * 0.6))
    val_end = train_end + int(round(n_candles * 0.2))
    period = "IN"
    if ei is not None:
        if ei >= train_end and ei < val_end:
            period = "VALIDATION"
        elif ei >= val_end:
            period = "OOS"
    rec = {
        "trade_id": t.trade_id, "setup_type": t.setup_type, "regime": t.regime,
        "side": side, "score": t.score, "score_norm": t.score_normalized,
        "entry_bar": ei, "exit_bar": xi, "holding_bars": t.holding_bars,
        "exit_reason": t.exit_reason, "period": period,
        "net_return": t.net_return,
        "bars_to_mfe": None, "bars_to_mae": None,
        "fwd_first": None,
    }
    if ei is None or xi is None or ref is None or ref <= 0:
        rec["incomplete"] = True
        return rec
    # geometry from detection (same source the simulator's _levels uses)
    stop = target = None
    target_pre = None
    if sd:
        if side == "LONG":
            stop = sd["ia_low"]; candidate_target = sd["target_high"]
        else:
            stop = sd["ia_high"]; candidate_target = sd["target_low"]
        if stop is not None and stop <= 0:
            stop = None
        target_pre = candidate_target
        # target usable only on profit side of the (unadjusted) entry ref
        if candidate_target is not None:
            if side == "LONG" and candidate_target > ref:
                target = candidate_target
            if side == "SHORT" and candidate_target < ref:
                target = candidate_target
    rec["entry_ref"] = ref
    rec["stop"] = stop
    rec["target"] = target
    rec["target_pre"] = target_pre
    if stop is not None:
        rec["stop_dist_pct"] = abs(ref - stop) / ref
    else:
        rec["stop_dist_pct"] = None
    if target is not None:
        rec["target_dist_pct"] = abs(ref - target) / ref
    else:
        rec["target_dist_pct"] = None
    if stop is not None and target is not None and ref is not None:
        rec["rew_risk"] = abs(target - ref) / abs(ref - stop) if abs(ref - stop) > 0 else None
    else:
        rec["rew_risk"] = None

    # walk the realized holding path [ei..xi]
    n = len(df)
    mfe = 0.0; mae = 0.0; mfe_bar = ei; mae_bar = ei
    hi_arr = df["high"].to_numpy(); lo_arr = df["low"].to_numpy(); cl_arr = df["close"].to_numpy()
    for b in range(ei, min(xi + 1, n)):
        f = (hi_arr[b] - ref) if side == "LONG" else (ref - lo_arr[b])
        a = (ref - lo_arr[b]) if side == "LONG" else (hi_arr[b] - ref)
        if f > mfe:
            mfe = f; mfe_bar = b
        if a > mae:
            mae = a; mae_bar = b
    rec["bars_to_mfe"] = mfe_bar - ei
    rec["bars_to_mae"] = mae_bar - ei
    holding = max(1, t.holding_bars)
    # favourable-first if MFE occurred before MAE (or before the stop was hit)
    if rec["bars_to_mfe"] is not None and rec["bars_to_mae"] is not None:
        if rec["bars_to_mfe"] < rec["bars_to_mae"]:
            rec["fwd_first"] = "FAVORABLE_FIRST"
        elif rec["bars_to_mfe"] > rec["bars_to_mae"]:
            rec["fwd_first"] = "ADVERSE_FIRST"
        else:
            rec["fwd_first"] = "SAME_BAR"

    # return at fractions of the holding (close-based, same fees) - only when holding>=2
    frac_ret = {}
    pb = t.entry_ts
    ep = t.entry_price
    for f in FRACTIONS:
        target_bar = ei + max(0, int(f * holding))
        if target_bar >= n:
            target_bar = n - 1
        if target_bar <= ei:
            frac_ret[f] = None
        else:
            frac_ret[f] = _net_at_close(cl_arr[target_bar], ep, side)
    rec["frac_ret"] = frac_ret
    # final return before timeout (== net_return for EXPIRED)
    rec["ret_at_100"] = frac_ret.get(1.00)

    # counterfactual: descriptive labels from the path AFTER the realized exit,
    # up to +COUNTERFACTUAL_BARS. Never re-simulated, never a decision input.
    cf = {"later_hit_target": False, "later_hit_stop": False,
          "max_future_mfe_pct": None, "max_future_mae_pct": None}
    if target is not None or stop is not None:
        scan_end = min(xi + COUNTERFACTUAL_BARS + 1, n)
        fut_mfe = 0.0; fut_mae = 0.0
        for b in range(xi + 1, scan_end):
            hh = hi_arr[b]; ll = lo_arr[b]
            if side == "LONG":
                if stop is not None and ll <= stop: cf["later_hit_stop"] = True
                if target is not None and hh >= target: cf["later_hit_target"] = True
                fut_mfe = max(fut_mfe, (hh - ref) / ref)
                fut_mae = max(fut_mae, (ref - ll) / ref)
            else:
                if stop is not None and hh >= stop: cf["later_hit_stop"] = True
                if target is not None and ll <= target: cf["later_hit_target"] = True
                fut_mfe = max(fut_mfe, (ref - ll) / ref)
                fut_mae = max(fut_mae, (hh - ref) / ref)
        cf["max_future_mfe_pct"] = fut_mfe if fut_mfe > 0 else None
        cf["max_future_mae_pct"] = fut_mae if fut_mae > 0 else None
    rec["counterfactual"] = cf
    return rec


def per_trade_frame(symbol, timeframe, run, geometry, df, settings):
    interval = settings.timeframe_interval_seconds.get(timeframe, 14400)
    ts_to_idx = {int(t): i for i, t in enumerate(df["open_time"])}
    rows = []
    for t in run.trades:
        sd = geometry.get((t.setup_type, t.detection_ts))
        rows.append(trade_forensics(symbol, timeframe, t, df, ts_to_idx, interval, sd, len(df)))
    return rows


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, bool):
        return bool(o)
    if isinstance(o, (int, float)):
        if isinstance(o, (int,)) or (isinstance(o, float) and o.is_integer()):
            return int(o)
        if isinstance(o, float):
            return float(o) if not (math.isnan(o) or math.isinf(o)) else None
    return o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="e.g. 'BTC/USDT 4H'")
    a = ap.parse_args()
    settings = get_settings()
    datasets = [("BTC/USDT", "1D"), ("ETH/USDT", "1D"), ("BTC/USDT", "4H"), ("ETH/USDT", "4H")]
    if a.only:
        datasets = [tuple(a.only.split(" "))]

    dest = Path(__file__).resolve().parents[3] / "reports" / "phase5_1" / "_e7_data.json"
    payload = {"meta": {"baseline": PHASE5_BASELINE_VERSION, "counterfactual_bars": COUNTERFACTUAL_BARS}}
    if dest.exists():
        payload = json.loads(dest.read_text(encoding="utf-8"))

    for sym, tf in datasets:
        store = CandleStore(settings)
        df = store.load(sym, tf).sort_values("open_time").reset_index(drop=True)
        run = baseline_run(settings, sym, tf, df)
        geom = detection_geometry(settings, sym, tf, df)
        rows = per_trade_frame(sym, tf, run, geom, df, settings)
        print(f"{sym} {tf}: trades={len(run.trades)} setups={len(run.setups)} candles={len(df)} "
              f"geometry_joined={sum(1 for r in rows if r.get('stop') is not None or r.get('target') is not None)}/{len(rows)}",
              flush=True)
        payload[f"{sym} {tf}"] = _clean(rows)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print("Done all requested datasets")


if __name__ == "__main__":
    main()