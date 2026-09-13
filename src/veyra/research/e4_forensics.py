"""E4 — Setup Engine Forensics (diagnostic only, no strategy change).

Captures a forensic dataset from the UNMODIFIED frozen baseline engine and
reports why the setup engine behaves as it does.

Instrumentation strategy (PURELY OBSERVATIONAL, behavior-neutral):
  - Wrap MarketAnalysisPipeline.analyze_each to record every per-bar snapshot
    (the same causal snapshots the baseline engine already computes).
  - Patch SetupEngine.advance to log every lifecycle transition (setup, new
    state, snapshot timestamp, regime, momentum) — used to attribute EXPIRED
    to age vs regime without changing lifecycle behaviour.
  - Patch Simulator.register to capture each domain Setup (component scores,
    overall score, side, regime, interest area, targets) + detection bar.
  - Patch Simulator.record_all to snapshot final tracked lifecycle states.
  - Re-join the returned BacktestTrade / SetupRecord for returns, MAE/MFE and
    the canonical funnel counts.

NOTHING in src/veyra/strategy or src/veyra/market or src/veyra/backtest is
modified. The baseline engine is never modified. No new strategy parameters.

Usage:
    python src/veyra/research/e4_forensics.py
    python src/veyra/research/e4_forensics.py --fast   # 1D only
"""
from __future__ import annotations

import json
import sys
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from veyra.backtest import BacktestEngine, MetricsEngine
from veyra.backtest.models import BacktestTrade, SetupRecord
from veyra.backtest.simulator import Simulator
from veyra.config import get_settings
from veyra.data.candle_store import CandleStore
from veyra.domain import AnalyticsComponent, SetupState
from veyra.market.pipeline import MarketAnalysisPipeline
from veyra.phase5.frozen_config import PHASE5_BASELINE_VERSION
from veyra.strategy.setup_engine import SetupEngine

E4_VERSION = "e4-forensics-v1"

COMPONENTS = ["TREND", "STRUCTURE", "PULLBACK", "MOMENTUM", "VOLUME", "VOLATILITY"]
WEIGHTS = {
    "TREND": 0.25, "STRUCTURE": 0.20, "PULLBACK": 0.15,
    "MOMENTUM": 0.12, "VOLUME": 0.07, "VOLATILITY": 0.05,
}
WEIGHT_SUM = 0.84


class SnapshotRecorder:
    """Wraps a pipeline and stores every causal snapshot (behavior-neutral)."""

    def __init__(self, pipeline: MarketAnalysisPipeline) -> None:
        self._pipeline = pipeline
        self.snapshots: List = []          # MarketSnapshot per bar, index-aligned

    @property
    def _engines(self):
        # Delegate to the wrapped pipeline so the engine's warm-up calc works
        # (behavior-neutral passthrough).
        return self._pipeline._engines

    def analyze_each(self, symbol: str, timeframe: str, data: pd.DataFrame):
        out = self._pipeline.analyze_each(symbol, timeframe, data)
        self.snapshots = out
        return out


def _meta(snapshot, name):
    comp = snapshot.components.get(name)
    return dict(comp.meta) if comp else {}


def run_forensics(symbol: str, timeframe: str) -> dict:
    settings = get_settings()
    store = CandleStore(settings)
    df = store.load(symbol, timeframe)
    df = df.sort_values("open_time").reset_index(drop=True)
    times = df["open_time"].to_numpy()
    n = len(df)
    ts_to_idx = {int(ts): i for i, ts in enumerate(times)}

    # Instrumented baseline engine (identical behaviour; only observation added).
    pipeline = SnapshotRecorder(MarketAnalysisPipeline.default(settings))
    engine = BacktestEngine(
        settings,
        pipeline=pipeline,
        strategy_version=PHASE5_BASELINE_VERSION,
        progressive=True,
    )

    # --- state captured during the run --------------------------------
    setup_by_id: Dict[int, object] = {}      # id(domain Setup) -> domain Setup
    key_by_id: Dict[int, str] = {}           # id(Setup) -> simulator key
    det_bar_by_id: Dict[int, int] = {}       # id(Setup) -> detection bar
    transitions: List[dict] = []             # lifecycle transitions
    final_tracked: List[dict] = []           # snapshot of _tracked at end

    orig_advance = SetupEngine.advance

    def patched_advance(self, setup, snapshot):
        new_state = orig_advance(self, setup, snapshot)
        if new_state is not None:
            transitions.append({
                "setup_id": id(setup),
                "from": setup.state.value,
                "new": new_state.value,
                "ts": int(snapshot.timestamp),
                "regime": snapshot.regime.value,
                "momentum": _meta(snapshot, "MOMENTUM").get("momentum", "UNKNOWN"),
            })
        return new_state

    orig_register = Simulator.register

    def patched_register(self, setup, bar_index):
        setup_by_id[id(setup)] = setup
        key = orig_register(self, setup, bar_index)
        key_by_id[id(setup)] = key
        det_bar_by_id[id(setup)] = bar_index
        return key

    orig_record_all = Simulator.record_all

    def patched_record_all(self, final_bar_ts, bar_index, normalizer):
        out = orig_record_all(self, final_bar_ts, bar_index, normalizer)
        for key, ts in self._tracked.items():
            final_tracked.append({
                "key": key,
                "state": ts.setup.state.value,
                # _finalize sets final_state=COMPLETED when a trade closes;
                # terminal transitions set final_state in engine step 4.
                "final_state": ts.final_state or ts.setup.state.value,
                "detection_bar": ts.detection_bar,
                "entry_bar": ts.entry_bar,
                "qualification_ts": ts.qualification_ts,
                "trade_id": ts.trade_id,
            })
        return out

    with patch.object(SetupEngine, "advance", new=patched_advance), \
         patch.object(Simulator, "register", new=patched_register), \
         patch.object(Simulator, "record_all", new=patched_record_all):
        run = engine.run(symbol, timeframe, df.copy(),
                         run_key=f"e4|{symbol}|{timeframe}")

    snap_by_idx = pipeline.snapshots

    # Build setup_id -> final tracked info (dedupe the most recent snapshot).
    final_by_key: Dict[str, dict] = {}
    for ft in final_tracked:
        final_by_key[ft["key"]] = ft

    # --- Build per-setup forensic rows --------------------------------
    rows: List[dict] = []
    trade_by_key: Dict[str, BacktestTrade] = {t.setup_key: t for t in run.trades}
    record_by_key: Dict[str, SetupRecord] = {s.key: s for s in run.setups}

    for setup_id, setup in setup_by_id.items():
        key = key_by_id[setup_id]
        ft = final_by_key.get(key, {})
        rec = record_by_key.get(key)
        det_bar = det_bar_by_id.get(setup_id)

        # qualification bar index: last transition to QUALIFIED for this setup
        qual_ts = ft.get("qualification_ts")
        qual_idx = ts_to_idx.get(qual_ts) if qual_ts is not None else None

        # find state-progress timestamps from transitions
        st_ts = {}
        for tr in transitions:
            if tr["setup_id"] == setup_id:
                st_ts[tr["new"]] = tr["ts"]
        qual_ts_from_tr = st_ts.get("QUALIFIED")

        # market evidence at detection
        det_snap = snap_by_idx[det_bar] if det_bar is not None and det_bar < len(snap_by_idx) else None
        # market evidence at qualification
        qual_bar = qual_idx if qual_idx is not None else None
        qual_snap = snap_by_idx[qual_bar] if qual_bar is not None and qual_bar < len(snap_by_idx) else None
        if qual_snap is None and qual_ts_from_tr is not None:
            qb = ts_to_idx.get(qual_ts_from_tr)
            qual_snap = snap_by_idx[qb] if qb is not None and qb < len(snap_by_idx) else None

        side_v = setup.side.value if setup.side is not None else "?"
        price = det_snap.components.get("STRUCTURE").value if det_snap else None

        score_en = {c: setup.scores.get(AnalyticsComponent(c), 0) for c in COMPONENTS}
        raw_total = sum(
            score_en[c] * WEIGHTS[c] for c in COMPONENTS
        )
        norm = round(raw_total / WEIGHT_SUM) if raw_total else 0

        trade = trade_by_key.get(key)
        rec_trade_id = rec.trade_id if rec is not None else None
        row = {
            "setup_id": setup_id,
            "key": key,
            "setup_type": rec.setup_type if rec else setup.setup_type.value,
            "side": rec.side if rec else side_v,
            "regime": rec.regime if rec else setup.regime.value,
            "detection_ts": rec.detection_ts if rec else setup.timestamp,
            "detection_bar": det_bar,
            "qualification_ts": ft.get("qualification_ts"),
            "entry_bar": ft.get("entry_bar"),
            "final_state": rec.final_state if rec else (ft.get("final_state") or "DETECTED"),
            "outcome": rec.outcome if rec else "DETECTED_ONLY",
            "invalidation": rec.invalidation if rec else (setup.invalidation or ""),
            "trade_id": rec_trade_id or ft.get("trade_id"),
            "entered": rec is not None and rec_trade_id is not None,
            "interest_area_low": rec.interest_area_low if rec else None,
            "interest_area_high": rec.interest_area_high if rec else None,
            "score_raw_total": raw_total,
            "score_norm": norm,
            "score_norm_record": rec.score_normalized if rec else None,
            "scores": score_en,
        }
        # market evidence at qualification
        if qual_snap is not None:
            sm = _meta(qual_snap, "STRUCTURE")
            tm = _meta(qual_snap, "TREND")
            mom = _meta(qual_snap, "MOMENTUM")
            vm = _meta(qual_snap, "VOLUME")
            vom = _meta(qual_snap, "VOLATILITY")
            qprice = qual_snap.components.get("STRUCTURE").value
            atr = vom.get("atr")
            rsi = mom.get("rsi")
            macd_hist = mom.get("macd_histogram")
            rel_vol = vm.get("relative_volume")
            row.update({
                "q_price": qprice,
                "q_atr": atr,
                "q_atr_pct": (atr / qprice if atr and qprice else None),
                "q_rsi": rsi,
                "q_macd_hist": macd_hist,
                "q_rel_vol": rel_vol,
                "q_ema_fast": tm.get("ema_fast"),
                "q_ema_slow": tm.get("ema_slow"),
                "q_ema_fast_slope": tm.get("ema_fast_slope"),
                "q_ema_slow_slope": tm.get("ema_slow_slope"),
                "q_structure": sm.get("structure"),
                "q_action": sm.get("action"),
                "q_last_swing_high": sm.get("last_swing_high"),
                "q_last_swing_low": sm.get("last_swing_low"),
                "q_regime": qual_snap.regime.value,
                "q_momentum": mom.get("momentum"),
                "q_volume_state": vm.get("volume_state"),
                "q_volatility_state": vom.get("volatility_state"),
            })
            if qprice is not None and sm.get("last_swing_high") is not None:
                row["q_dist_to_res"] = sm["last_swing_high"] - qprice
            if qprice is not None and sm.get("last_swing_low") is not None:
                row["q_dist_to_sup"] = qprice - sm["last_swing_low"]
            else:
                row["q_dist_to_sup"] = None
        else:
            row.update({
                "q_price": None, "q_atr": None, "q_atr_pct": None, "q_rsi": None,
                "q_macd_hist": None, "q_rel_vol": None, "q_ema_fast": None,
                "q_ema_slow": None, "q_ema_fast_slope": None, "q_ema_slow_slope": None,
                "q_structure": None, "q_action": None, "q_last_swing_high": None,
                "q_last_swing_low": None, "q_regime": None, "q_momentum": None,
                "q_volume_state": None, "q_volatility_state": None,
                "q_dist_to_res": None, "q_dist_to_sup": None,
            })

        # trade outcomes when entered
        if trade is not None:
            row.update({
                "entry_ts": trade.entry_ts,
                "exit_reason": trade.exit_reason,
                "net_return": trade.net_return,
                "holding_bars": trade.holding_bars,
                "mae": trade.max_adverse_excursion,
                "mfe": trade.max_favorable_excursion,
                "score_norm_trade": trade.score_normalized,
            })
        rows.append(row)

    out = {
        "symbol": symbol,
        "timeframe": timeframe,
        "rows": rows,
        "n_candles": n,
    }
    return out


def _pf(rets):
    gw = sum(r for r in rets if r > 0)
    gl = abs(sum(r for r in rets if r < 0))
    if gl > 0:
        return gw / gl
    if gw > 0:
        return float("inf")
    return None


def _fnum(v):
    if isinstance(v, float) and math.isinf(v):
        return "  inf"
    if v is None:
        return "  N/A"
    return f"{v:.4f}"


# ── Analysis helpers used by report sections --------------------------

def funnel(rows):
    """Canonical funnel keyed on SetupOutcome (same as the baseline/validate.py)."""
    o = Counter(r["outcome"] for r in rows)
    total = len(rows)
    qual = o.get("QUALIFIED_NO_TRADE", 0)
    comp = o.get("COMPLETED", 0)
    exp = o.get("EXPIRED", 0)
    inv = o.get("INVALIDATED", 0)
    det = o.get("DETECTED_ONLY", 0)
    qual_set = qual + comp
    ent = comp
    return {
        "detected": total,
        "detected_only": det,
        "qualified_no_trade": qual,
        "completed_traded": comp,
        "expired": exp,
        "invalidated": inv,
        "entered": ent,
        "qualified_total": qual_set,
        "pct_qualified": (qual_set / total * 100) if total else 0,
        "pct_entered_of_qualified": (ent / qual_set * 100) if qual_set else 0,
    }


def expiration_breakdown(rows):
    # EXPIRED split by lifecycle stage using canonical outcome + qualification.
    out = Counter()
    for r in rows:
        if r["outcome"] == "COMPLETED":
            if r.get("exit_reason") == "EXPIRED":
                out["after_entry_position_age"] += 1
            continue
        if r["outcome"] == "INVALIDATED":
            out["invalidated"] += 1
            continue
        if r["outcome"] == "EXPIRED":
            if r["qualification_ts"] is not None:
                out["after_qual_before_entry"] += 1
            else:
                out["before_qualification"] += 1
    return dict(out)


# ── Aggregated analyses for report sections --------------------------

def bucket_of(score):
    if score is None:
        return "no_score"
    for low, high in [(0, 19), (20, 39), (40, 59), (60, 79), (80, 100)]:
        if low <= score <= high:
            return f"{low}-{high}"
    return "no_score"


def by_type(rows):
    agg = {}
    for r in rows:
        t = r["setup_type"]
        d = agg.setdefault(t, {"n": 0, "expired": 0, "invalidated": 0,
                                "completed": 0, "unqualified": 0,
                                "returns": [], "mfe": [], "mae": [],
                                "score_norms": [], "holding": [], "why": Counter()})
        d["n"] += 1
        d["score_norms"].append(r["score_norm_record"] or r["score_norm"])
        d["why"][r["outcome"]] += 1
        if r["outcome"] == "EXPIRED":
            d["expired"] += 1
            if r["qualification_ts"] is None:
                d["unqualified"] += 1
        if r["outcome"] == "INVALIDATED":
            d["invalidated"] += 1
        if r["outcome"] == "COMPLETED":
            d["completed"] += 1
            if r.get("net_return") is not None:
                d["returns"].append(r["net_return"])
            if r.get("mfe") is not None:
                d["mfe"].append(r["mfe"])
            if r.get("mae") is not None:
                d["mae"].append(r["mae"])
            if r.get("holding_bars") is not None:
                d["holding"].append(r["holding_bars"])
    out = {}
    for t, d in agg.items():
        n = d["n"]
        out[t] = {
            "n": n,
            "pct_of_detected": (n / len(rows) * 100) if rows else 0,
            "unqualified_before_qual": d["unqualified"],
            "qualified_no_trade": d["why"].get("QUALIFIED_NO_TRADE", 0),
            "completed": d["completed"],
            "expired": d["expired"],
            "invalidated": d["invalidated"],
            "avg_score": (sum(d["score_norms"]) / len(d["score_norms"])) if d["score_norms"] else None,
            "trade_pf": _pf(d["returns"]) if d["returns"] else None,
            "trade_winrate": (sum(1 for x in d["returns"] if x > 0) / len(d["returns"]) * 100) if d["returns"] else None,
            "avg_ret": (sum(d["returns"]) / len(d["returns"])) if d["returns"] else None,
            "avg_mfe_px": (sum(d["mfe"]) / len(d["mfe"])) if d["mfe"] else None,
            "avg_mae_px": (sum(d["mae"]) / len(d["mae"])) if d["mae"] else None,
            "avg_hold_bars": (sum(d["holding"]) / len(d["holding"])) if d["holding"] else None,
        }
    return out


def score_buckets(rows):
    buckets = {}
    for r in rows:
        b = bucket_of(r["score_norm_record"] or r["score_norm"])
        d = buckets.setdefault(b, {"n": 0, "completed": 0, "expired": 0,
                                    "invalidated": 0, "returns": []})
        d["n"] += 1
        if r["outcome"] == "COMPLETED":
            d["completed"] += 1
            if r.get("net_return") is not None:
                d["returns"].append(r["net_return"])
        if r["outcome"] == "EXPIRED":
            d["expired"] += 1
        if r["outcome"] == "INVALIDATED":
            d["invalidated"] += 1
    out = {}
    for b, d in buckets.items():
        out[b] = {
            "n": d["n"],
            "n_completed": d["completed"],
            "n_expired": d["expired"],
            "n_invalidated": d["invalidated"],
            "qualify_to_trade_pct": (d["completed"] / d["n"] * 100) if d["n"] else None,
            "avg_ret": (sum(d["returns"]) / len(d["returns"])) if d["returns"] else None,
            "winrate": (sum(1 for x in d["returns"] if x > 0) / len(d["returns"]) * 100) if d["returns"] else None,
        }
    return out


def component_distribution(rows):
    comps = {}
    for c in COMPONENTS:
        vals = [r["scores"].get(c, 0) for r in rows if r["scores"].get(c) is not None]
        if vals:
            comps[c] = {
                "n": len(vals),
                "mean": sum(vals) / len(vals),
                "zero": sum(1 for v in vals if v == 0),
                "nonzero": sum(1 for v in vals if v != 0),
            }
    # momentum opposed / neutral frequency
    mom_neutral = sum(1 for r in rows if r.get("q_momentum") == "NEUTRAL")
    mom_pos = sum(1 for r in rows if r.get("q_momentum") in ("POSITIVE",))
    mom_neg = sum(1 for r in rows if r.get("q_momentum") in ("NEGATIVE",))
    comps["_momentum_state"] = {
        "POSITIVE": mom_pos, "NEGATIVE": mom_neg, "NEUTRAL": mom_neutral,
        "n_with_metadata": mom_pos + mom_neg + mom_neutral,
    }
    # structure states at qualification
    struct_cnt = Counter(r.get("q_structure") for r in rows)
    comps["_structure_state"] = {k: int(v) for k, v in struct_cnt.items() if k}
    return comps


def qualify_to_outcome(rows):
    qualified = [r for r in rows if r["qualification_ts"] is not None]
    out = Counter(r["outcome"] for r in qualified)
    return {
        "qualified": len(qualified),
        **{k: int(v) for k, v in out.items()},
        "of_qualified_to_trade": (out.get("COMPLETED", 0) / len(qualified) * 100) if qualified else None,
    }


def mae_mfe_by_bucket(rows):
    out = {}
    for r in rows:
        if r["outcome"] != "COMPLETED":
            continue
        b = bucket_of(r["score_norm_record"] or r["score_norm"])
        d = out.setdefault(b, {"n": 0, "mfe_px": [], "mae_px": [], "mfe_mae_ratio": [],
                                "ret": [], "hold": []})
        d["n"] += 1
        if r.get("mfe") is not None and r.get("mae") is not None:
            d["mfe_px"].append(r["mfe"])
            d["mae_px"].append(r["mae"])
            if r["mae"] > 0:
                d["mfe_mae_ratio"].append(r["mfe"] / r["mae"])
        if r.get("net_return") is not None:
            d["ret"].append(r["net_return"])
        if r.get("holding_bars") is not None:
            d["hold"].append(r["holding_bars"])
    for b, d in out.items():
        d["avg_mfe"] = (sum(d["mfe_px"]) / len(d["mfe_px"])) if d["mfe_px"] else None
        d["avg_mae"] = (sum(d["mae_px"]) / len(d["mae_px"])) if d["mae_px"] else None
        d["avg_mfe_mae_ratio"] = (sum(d["mfe_mae_ratio"]) / len(d["mfe_mae_ratio"])) if d["mfe_mae_ratio"] else None
        d["avg_ret"] = (sum(d["ret"]) / len(d["ret"])) if d["ret"] else None
        d["avg_hold"] = (sum(d["hold"]) / len(d["hold"])) if d["hold"] else None
        d.pop("mfe_px", None); d.pop("mae_px", None)
        d.pop("mfe_mae_ratio", None); d.pop("ret", None); d.pop("hold", None)
    return out


def setup_age_profile(rows):
    # age from detection to expiry/entry, in bars
    ages = []
    for r in rows:
        d = r["detection_bar"]
        e = r["entry_bar"]
        if e is not None:
            ages.append(("to_entry", e - d))
        elif r["outcome"] == "EXPIRED":
            ages.append(("expired_no_entry", None))
    by_stage = Counter(a[0] for a in ages)
    to_entry = [a[1] for a in ages if a[0] == "to_entry" and a[1] is not None]
    return {
        "to_entry_counts": by_stage["to_entry"],
        "expired_no_entry_counts": by_stage["expired_no_entry"],
        "avg_det_to_entry_bars": (sum(to_entry) / len(to_entry)) if to_entry else None,
        "min_det_to_entry_bars": min(to_entry) if to_entry else None,
        "max_det_to_entry_bars": max(to_entry) if to_entry else None,
        "expired_fraction": by_stage["expired_no_entry"] / len(rows) if rows else None,
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true")
    a = ap.parse_args()

    datasets = [("BTC/USDT", "1D"), ("ETH/USDT", "1D")]
    if not a.fast:
        datasets += [("BTC/USDT", "4H"), ("ETH/USDT", "4H")]

    results = {}
    for sym, tf in datasets:
        print(f"\n=== FORENSICS {sym} {tf} ===")
        res = run_forensics(sym, tf)
        res["funnel"] = funnel(res["rows"])
        res["expiration"] = expiration_breakdown(res["rows"])
        res["by_type"] = by_type(res["rows"])
        res["score_buckets"] = score_buckets(res["rows"])
        res["component_distribution"] = component_distribution(res["rows"])
        res["qualify_to_outcome"] = qualify_to_outcome(res["rows"])
        res["mae_mfe_by_bucket"] = mae_mfe_by_bucket(res["rows"])
        res["age_profile"] = setup_age_profile(res["rows"])
        results[f"{sym} {tf}"] = res
        print("FUNNEL:", {k: res["funnel"][k] for k in ("detected","qualified_total","completed_traded","expired","invalidated","qualified_no_trade")})
        print("EXPIRATION:", res["expiration"])
        print("RESULTS counts: expired=%d  position_age_exits=%d  before_qual=%d" % (
            res["funnel"]["expired"],
            res["expiration"].get("after_entry_position_age", 0),
            res["expiration"].get("before_qualification", 0)))
        print("QUALIFY->OUTCOME:", res["qualify_to_outcome"])

    # Serialize for downstream report authoring (rows contain numpy floats).
    def _clean(obj):
        if isinstance(obj, dict):
            return {str(k): _clean(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_clean(v) for v in obj]
        if isinstance(obj, (float, int)) and not isinstance(obj, bool):
            if isinstance(obj, float):
                if math.isinf(obj):
                    return None if obj > 0 else None
                if math.isnan(obj):
                    return None
            return float(obj) if isinstance(obj, float) else int(obj)
        if isinstance(obj, str) and obj in ("nan", "inf", "-inf"):
            return None
        return str(obj)

    payload = {k: {kk: _clean(vv) for kk, vv in v.items()} for k, v in results.items()}
    dest_dir = Path(__file__).resolve().parents[3] / "reports" / "phase5_1"
    dest_dir.mkdir(parents=True, exist_ok=True)
    with open(dest_dir / "_e4_forensics_data.json", "w") as fh:
        json.dump(payload, fh, indent=1)
    print("\nWrote reports/phase5_1/_e4_forensics_data.json")