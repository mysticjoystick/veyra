"""E6 — Pre-Gate Evidence vs Forward Outcome (diagnostic only).

Central question:
    Before the qualification gate compresses the candidate population, do
    Veyra's existing (genuine) evidence components contain a stable
    relationship with future trade quality?

Hard rules honoured:
  * Research/diagnostic ONLY. No production code is modified.
  * Strategy 100% frozen (`phase5-baseline-v1`): detection/qualification/entry/
    exit run the exact baseline machinery. We only *observe* component scores
    and compute forward *labels*; we never change behavior.
  * No threshold/weight/detector/exit tuning, no sweeps, no new indicators.
  * Two distinct measurement axes:
        A) RAW candidate -> FUTURE MARKET outcome (forward return / MFE / MAE
           over a fixed window, computed for EVERY detected candidate whether or
           not it later qualified/traded). PRIMARY E6 question.
        B) candidate -> qualification -> trade outcome (the frozen run's
           eventual outcome / exit reason), joined deterministically.
  * Every *feature* is detection-time and causal. Forward outcomes are the
    *label* being predicted (allowed).
  * IN 60% / VAL 20% / OOS 20% used only to test stability, never to tune.

Features at detection (all causal, from the per-bar snapshot):
  * timestamp, asset, timeframe, LONG/SHORT, setup family, regime
  * genuine TREND/STRUCTURE/MOMENTUM/VOLUME (from AnalysisComponentOutput.score,
    the engine evidence E5/H1 reconnects) and VOLATILITY/PULLBACK (existing
    setup-scorer rules).
  * baseline overall score (raw + normalized) for reference.

Forward labels (A):
  * fwd_ret_5 / fwd_ret_10 : signed close-to-close return over a fixed horizon.
  * fwd_mfe_pct / fwd_mae_pct : signed max favorable/adverse excursion vs the
    detection close over a cap of 60 bars (aligned to backtest_position_max_bars).
  * fwd_target_reached / fwd_stop_reached : whether the future range crossed the
    candidate's own target/stop before the last bar.

Backtest labels (B), joined from ONE frozen build_baseline_engine run:
  * period (IN/VAL/OOS), qualified, traded, outcome, exit_reason, net_return,
    holding_bars, mfe, mae (present only when it traded).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import List

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from veyra.backtest.engine import BacktestEngine
from veyra.config import get_settings
from veyra.data.candle_store import CandleStore
from veyra.domain import AnalyticsComponent, MarketSide
from veyra.domain.setup import Setup
from veyra.market.pipeline import MarketAnalysisPipeline
from veyra.phase5.frozen_config import build_baseline_engine, PHASE5_BASELINE_VERSION
from veyra.strategy.detectors import rules
from veyra.strategy.setup_engine import SetupEngine
from veyra.strategy.snapshot_view import SnapshotView

HORIZON = 60   # forward cap, aligned to backtest_position_max_bars
SHORT_H = 10
PROMPT_H = 5
COMPONENTS = ["TREND", "STRUCTURE", "MOMENTUM", "VOLUME", "VOLATILITY", "PULLBACK"]


def _real_score(view: SnapshotView, name: str) -> int:
    comp = view._snapshot.components.get(name)
    if comp is None or comp.score is None:
        return 0
    return max(0, min(100, int(comp.score)))


def component_scores(view: SnapshotView, side) -> dict:
    s = {}
    # TREND
    s["TREND"] = _real_score(view, "TREND")
    # STRUCTURE
    struct = view.meta("STRUCTURE")
    v = _real_score(view, "STRUCTURE")
    opposed = (
        struct.get("structure") in ("LH_LL",) if side == MarketSide.LONG
        else struct.get("structure") in ("HH_HL",)
    )
    if opposed:
        v = max(0, v - 40)
    s["STRUCTURE"] = v
    # MOMENTUM
    mom = view.meta("MOMENTUM")
    v = _real_score(view, "MOMENTUM")
    opposed = (
        mom.get("momentum") == "NEGATIVE" if side == MarketSide.LONG
        else mom.get("momentum") == "POSITIVE"
    )
    if opposed:
        v = max(0, v - 50)
    s["MOMENTUM"] = v
    # VOLUME
    vol = view.meta("VOLUME")
    v = _real_score(view, "VOLUME")
    if vol.get("price_volume_confirmation"):
        v = min(100, v + 15)
    s["VOLUME"] = v
    # VOLATILITY (existing setup-scorer inverse state map)
    vs = view.meta("VOLATILITY").get("volatility_state", "UNKNOWN")
    s["VOLATILITY"] = {"EXTREME": 15, "HIGH": 40, "NORMAL": 75, "LOW": 90, "UNKNOWN": 0}.get(vs, 0)
    # PULLBACK (existing setup-scorer rule)
    st = rules.structure_state(view)
    mo = rules.momentum_state(view)
    aligned = (
        st in ("HH_HL",) and mo == "POSITIVE" if side == MarketSide.LONG
        else st in ("LH_LL",) and mo == "NEGATIVE"
    )
    s["PULLBACK"] = 85 if aligned else 40
    return s


def searchsorted(arr, x):
    lo, hi = 0, len(arr)
    while lo < hi:
        mid = (lo + hi) // 2
        if arr[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo


def forward_labels(df, i, side, stop, target, horizon=HORIZON):
    n = len(df)
    close = df["close"].to_numpy()
    hi = df["high"].to_numpy()
    lo = df["low"].to_numpy()
    c0 = float(close[i])
    if c0 <= 0 or i >= n - 1:
        return None
    j = min(i + horizon, n - 1)
    if i + 1 > j:
        return None
    win = hi[i + 1 : j + 1]
    loww = lo[i + 1 : j + 1]
    max_hi = float(win.max())
    min_lo = float(loww.min())
    if side == MarketSide.LONG:
        mfe = (max_hi - c0) / c0
        mae = (c0 - min_lo) / c0
    else:
        mfe = (c0 - min_lo) / c0
        mae = (max_hi - c0) / c0
    fwd_ret_10 = (float(close[min(i + SHORT_H, n - 1)]) - c0) / c0
    fwd_ret_5 = (float(close[min(i + PROMPT_H, n - 1)]) - c0) / c0
    tgt_reached = stop_reached = False
    if target is not None or stop is not None:
        for b in range(i + 1, j + 1):
            hh, ll = float(hi[b]), float(lo[b])
            if side == MarketSide.LONG:
                if stop is not None and ll <= stop:
                    stop_reached = True
                if target is not None and hh >= target:
                    tgt_reached = True
            else:
                if stop is not None and hh >= stop:
                    stop_reached = True
                if target is not None and ll <= target:
                    tgt_reached = True
    return {
        "fwd_ret_5": fwd_ret_5,
        "fwd_ret_10": fwd_ret_10,
        "fwd_mfe_pct": mfe,
        "fwd_mae_pct": mae,
        "fwd_target_reached": tgt_reached,
        "fwd_stop_reached": stop_reached,
    }


def detected_candidates(settings, symbol, timeframe):
    """Frozen pipeline + frozen SetupEngine; capture pre-gate candidates with
    detection-time component scores + forward market labels (A)."""
    store = CandleStore(settings)
    df = store.load(symbol, timeframe).sort_values("open_time").reset_index(drop=True)
    pipeline = MarketAnalysisPipeline.default(settings)
    snapshots = pipeline.analyze_each(symbol, timeframe, df)
    setup_engine = SetupEngine(settings)  # frozen default detectors

    warmup = max((e.warmup_required() for e in pipeline._engines.values()), default=0)
    n = len(df)

    rows = []
    for i in range(warmup, n):
        view = SnapshotView(snapshots[i])
        for d in setup_engine.detect(snapshots[i]):
            comp = component_scores(view, d.side)
            scored = {}
            for c in COMPONENTS:
                scored[c] = d.scores.get(c, 0)
            stop = None
            target = None
            if d.interest_area is not None:
                stop = (d.interest_area.low if d.side.value == "LONG" else d.interest_area.high)
            if d.targets:
                z = d.targets[0]
                target = (z.high if d.side.value == "LONG" else z.low)
            fwd = forward_labels(df, i, d.side, stop, target)
            row = {
                "symbol": symbol, "timeframe": timeframe,
                "timestamp": int(d.timestamp), "detection_idx": int(i),
                "setup_type": d.setup_type.value, "side": d.side.value,
                "regime": d.regime.value,
                "overall_score_raw": d.overall_score,
                "overall_score_norm": d.overall_score,
            }
            for c in COMPONENTS:
                row[f"genuine_{c}"] = int(comp[c])
                row[f"scored_{c}"] = int(scored[c])
            row["meta_missing_score"] = any(
                "score" not in (view.meta(c) if isinstance(view.meta(c), dict) else {})
                for c in ["TREND", "STRUCTURE", "MOMENTUM", "VOLUME", "VOLATILITY"]
            )
            if fwd is not None:
                row.update(fwd)
            rows.append(row)
    return pd.DataFrame(rows), df


def join_backtest(settings, symbol, timeframe, cand_df, df):
    """One frozen baseline run; join eventual lifecycle/outcome (B)."""
    engine = build_baseline_engine(settings, strategy_version=PHASE5_BASELINE_VERSION, progressive=True)
    run = engine.run(symbol, timeframe, df.copy(), run_key=f"e6|{symbol}|{timeframe}")
    rec_by_key = {(r.setup_type, r.detection_ts): r for r in run.setups}
    trades_by_id = {t.trade_id: t for t in run.trades}

    n = len(df)
    train_end = int(round(n * 0.6))
    val_end = train_end + int(round(n * 0.2))
    def _period(idx):
        return "IN" if idx < train_end else ("VALIDATION" if idx < val_end else "OOS")

    out = []
    joined = 0
    for _, row in cand_df.iterrows():
        r = rec_by_key.get((row["setup_type"], row["timestamp"]))
        rec = {"period": _period(row["detection_idx"])}
        if r is None:
            rec.update({"outcome": "UNJOINED", "qualified": False, "traded": False})
        else:
            joined += 1
            t = trades_by_id.get(r.trade_id) if r.trade_id else None
            rec.update({
                "outcome": getattr(r.outcome, "value", r.outcome),
                "qualified": getattr(r.outcome, "value", r.outcome) != "DETECTED_ONLY",
                "traded": getattr(r.outcome, "value", r.outcome) == "COMPLETED",
                "final_state": r.final_state,
            })
            if t is not None:
                rec.update({
                    "exit_reason": t.exit_reason,
                    "net_return": t.net_return,
                    "holding_bars": t.holding_bars,
                    "mfe": t.max_favorable_excursion,
                    "mae": t.max_adverse_excursion,
                })
        out.append(rec)
    rec_df = pd.DataFrame(out)
    rec_df.index = cand_df.index
    return rec_df, joined, len(cand_df)


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, bool):
        return bool(o)
    if isinstance(o, (int, float)):
        import numpy as np
        if isinstance(o, (np.integer, int)):
            return int(o)
        if isinstance(o, float):
            return float(o) if not (math.isnan(o) or math.isinf(o)) else None
    return o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--only", help="e.g. 'BTC/USDT 4H'")
    a = ap.parse_args()
    settings = get_settings()
    datasets = [("BTC/USDT", "1D"), ("ETH/USDT", "1D")]
    if not a.fast:
        datasets += [("BTC/USDT", "4H"), ("ETH/USDT", "4H")]
    if a.only:
        datasets = [tuple(a.only.split(" "))]

    dest = Path(__file__).resolve().parents[3] / "reports" / "phase5_1" / "_e6_data.json"
    payload = {}
    if dest.exists():
        payload = json.loads(dest.read_text(encoding="utf-8"))

    for sym, tf in datasets:
        print(f"\n=== {sym} {tf} ===", flush=True)
        cand, df = detected_candidates(settings, sym, tf)
        print(f"  detected candidates n={len(cand)}", flush=True)
        b, joined, total = join_backtest(settings, sym, tf, cand, df)
        print(f"  backtest join {joined}/{total}", flush=True)
        final_recs = cand.join(b).to_dict("records")
        payload[f"{sym} {tf}"] = _clean(final_recs)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
        print(f"  wrote {dest} ({len(final_recs)} rows)", flush=True)
    print("\nDone all requested datasets")


if __name__ == "__main__":
    main()