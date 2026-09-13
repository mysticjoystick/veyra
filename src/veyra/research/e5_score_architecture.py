"""E5 — H1 Score-Architecture Experiment (isolated, baseline untouched).

Single change ONLY: make the four currently-zero component scores (TREND,
STRUCTURE, MOMENTUM, VOLUME) contribute their genuine, already-computed
evidence scores, instead of reading a ``meta["score"]`` key that no market
engine emits. Everything else (weights, aggregation, normalisation, detector
logic, types, evidence, execution, lifecycle, thresholds) is identical.

Isolation:
  * ``H1SetupScorer`` subclasses ``SetupScorer`` and changes ONLY the four
    component readers to use ``AnalysisComponentOutput.score`` (the evidence
    score the market engines already compute and store).
  * An ``H1SetupEngine`` wraps the unchanged detectors/scoring wiring with the
    H1 scorer and is injected into ``BacktestEngine``. No production file
    (strategy/market/backtest) is modified. The frozen baseline engine is used
    unmodified as the control.

Protocol:
  * Freeze control = unmodified frozen baseline on the same 4 datasets.
  * Compare control vs H1 on the same chronological IN/VAL/OOS.
  * No score threshold is introduced. No parameter tuning. No sweeps.
"""
from __future__ import annotations

import itertools
import statistics
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
from veyra.domain import AnalyticsComponent, MarketSide
from veyra.data.candle_store import CandleStore
from veyra.market.pipeline import MarketAnalysisPipeline
from veyra.strategy.aggregation import WeightedScoreAggregator
from veyra.strategy.scoring import SetupScorer
from veyra.strategy.setup_engine import SetupEngine
from veyra.strategy.snapshot_view import SnapshotView

E5_VERSION = "e5-h1-score-v1"
CONTROL_VERSION = "phase5-baseline-v1"

COMPONENTS = ["TREND", "STRUCTURE", "PULLBACK", "MOMENTUM", "VOLUME", "VOLATILITY"]
PERIODS = [("IN", "training"), ("VALIDATION", "validation"), ("OOS", "test")]
BUCKET_LIMITS = [(0, 19), (20, 39), (40, 59), (60, 79), (80, 100)]


class H1SetupScorer(SetupScorer):
    """SetupScorer with the four component readers wired to real evidence scores.

    The ONLY change is that TREND / STRUCTURE / MOMENTUM / VOLUME read
    ``AnalysisComponentOutput.score`` (the genuine 0-100 evidence the market
    engine already computed) instead of the missing ``meta["score"]`` key.
    """

    @staticmethod
    def _real_score(view: SnapshotView, name: str) -> int:
        comp = view._snapshot.components.get(name)
        if comp is None or comp.score is None:
            return 0
        return max(0, min(100, int(comp.score)))

    def _trend(self, view: SnapshotView) -> int:
        return self._real_score(view, "TREND")

    def _structure(self, view: SnapshotView, side: MarketSide) -> int:
        struct = view.meta("STRUCTURE")
        value = self._real_score(view, "STRUCTURE")
        opposed = (
            struct.get("structure") in ("LH_LL",)
            if side == MarketSide.LONG
            else struct.get("structure") in ("HH_HL",)
        )
        if opposed:
            value = max(0, value - 40)
        return value

    def _momentum(self, view: SnapshotView, side: MarketSide) -> int:
        mom = view.meta("MOMENTUM")
        value = self._real_score(view, "MOMENTUM")
        opposed = (
            mom.get("momentum") == "NEGATIVE"
            if side == MarketSide.LONG
            else mom.get("momentum") == "POSITIVE"
        )
        if opposed:
            value = max(0, value - 50)
        return value

    def _volume(self, view: SnapshotView, side: MarketSide) -> int:
        volume = view.meta("VOLUME")
        value = self._real_score(view, "VOLUME")
        if not volume:
            return value
        if volume.get("price_volume_confirmation"):
            return min(100, value + 15)
        return value


class H1SetupEngine(SetupEngine):
    """The standard SetupEngine but with the H1 scorer injected."""

    def __init__(self, settings, detectors=None, aggregator=None):
        agg = aggregator or self._build_aggregator(settings)
        super().__init__(settings, detectors=detectors, aggregator=agg,
                         scorer=H1SetupScorer(agg))


def build_engine(settings, h1: bool, progressive=True):
    if h1:
        setup_engine = H1SetupEngine(settings)
        return BacktestEngine(
            settings,
            setup_engine=setup_engine,
            strategy_version=E5_VERSION,
            progressive=progressive,
        )
    # Control = the frozen Phase 5 baseline, exactly as validate.py builds it.
    from veyra.phase5.frozen_config import build_baseline_engine, PHASE5_BASELINE_VERSION
    return build_baseline_engine(settings, strategy_version=PHASE5_BASELINE_VERSION,
                                 progressive=progressive)


def run_variant(settings, symbol, timeframe, h1: bool) -> pd.DataFrame:
    store = CandleStore(settings)
    df = store.load(symbol, timeframe).sort_values("open_time").reset_index(drop=True)
    times = df["open_time"].to_numpy()
    engine = build_engine(settings, h1=h1)
    run = engine.run(symbol, timeframe, df.copy(),
                     run_key=f"e5|{'h1' if h1 else 'ctrl'}|{symbol}|{timeframe}")
    # Build a tidy per-setup frame with score buckets + outcome + trade join.
    recs = [s.to_dict() for s in run.setups]
    trades = {t.setup_key: t for t in run.trades}
    n = len(times)
    train_end = int(round(n * 0.6))
    val_end = train_end + int(round(n * 0.2))
    out = []
    for r in recs:
        t = trades.get(r["key"])
        row = {k: r[k] for k in (
            "key", "symbol", "timeframe", "detection_ts", "setup_type",
            "regime", "score", "score_normalized", "side", "final_state",
            "outcome", "trade_id")}
        idx = searchsorted(times, r["detection_ts"])
        if idx < train_end:
            row["period"] = "IN"
        elif idx < val_end:
            row["period"] = "VALIDATION"
        else:
            row["period"] = "OOS"
        if t is not None:
            row.update({
                "exit_reason": t.exit_reason,
                "net_return": t.net_return,
                "holding_bars": t.holding_bars,
                "mfe": t.max_favorable_excursion,
                "mae": t.max_adverse_excursion,
            })
        out.append(row)
    return pd.DataFrame(out)


def searchsorted(arr, x):
    lo, hi = 0, len(arr)
    while lo < hi:
        mid = (lo + hi) // 2
        if arr[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo


def bucket_of(score):
    if score is None:
        return "no_score"
    for lo, hi in BUCKET_LIMITS:
        if lo <= score <= hi:
            return f"{lo}-{hi}"
    return "no_score"


def _pf(rets):
    gw = sum(r for r in rets if r > 0)
    gl = abs(sum(r for r in rets if r < 0))
    if gl > 0:
        return gw / gl
    return (float("inf") if gw > 0 else None)


def score_distribution(rows: pd.DataFrame) -> dict:
    norm = rows["score_normalized"].dropna()
    out = {
        "n": int(len(norm)),
        "min": float(norm.min()),
        "median": float(norm.median()),
        "mean": float(norm.mean()),
        "max": float(norm.max()),
    }
    buckets = Counter(bucket_of(x) for x in norm)
    for lo, hi in BUCKET_LIMITS:
        key = f"{lo}-{hi}"
        out[f"bucket_{key}"] = int(buckets.get(key, 0))
        out[f"bucket_{key}_pct"] = (buckets.get(key, 0) / len(norm) * 100) if len(norm) else None
    return out


def bucket_outcomes(rows: pd.DataFrame) -> dict:
    # Existing score buckets vs outcomes (no threshold searched).
    grouped = rows.groupby(rows["score_normalized"].map(bucket_of))
    out = {}
    for b, g in grouped:
        traded = g[g["net_return"].notna()]
        rets = traded["net_return"].tolist()
        out[b] = {
            "n": int(len(g)),
            "qualified": int(len(g) - (g["outcome"] == "EXPIRED").sum()),
            "traded": int(len(traded)),
            "expired": int((g["outcome"] == "EXPIRED").sum()),
            "win_rate": (sum(1 for r in rets if r > 0) / len(rets) * 100) if rets else None,
            "avg_return": (sum(rets) / len(rets)) if rets else None,
            "profit_factor": _pf(rets) if rets else None,
            "avg_mae": float(traded["mae"].mean()) if len(traded) else None,
            "avg_mfe": float(traded["mfe"].mean()) if len(traded) else None,
            "avg_hold": float(traded["holding_bars"].mean()) if len(traded) else None,
            "expectancy": (sum(rets) / len(rets)) if rets else None,
        }
    return out


def component_distribution(rows: pd.DataFrame, has_scores=True) -> dict:
    # NOTE: SetupRecord does not carry per-component scores; this table is
    # filled from the E4-style instrumented capture in the report path.
    return {"note": "per-component handled via instrumented capture"}


def setup_behavior(rows: pd.DataFrame) -> dict:
    o = Counter(rows["outcome"])
    return {
        "detected": int(len(rows)),
        "qualified": int(len(rows) - (rows["outcome"] == "EXPIRED").sum()),
        "entered": int(o.get("COMPLETED", 0)),
        "completed": int(o.get("COMPLETED", 0)),
        "expired": int(o.get("EXPIRED", 0)),
        "invalidated": int(o.get("INVALIDATED", 0)),
        "never_entered": int((rows["outcome"] != "COMPLETED").sum()),
    }


if __name__ == "__main__":
    import argparse
    import json
    import numpy as np

    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--only", help="e.g. 'BTC/USDT 4H' to run a single dataset")
    a = ap.parse_args()

    datasets = [("BTC/USDT", "1D"), ("ETH/USDT", "1D")]
    if not a.fast:
        datasets += [("BTC/USDT", "4H"), ("ETH/USDT", "4H")]
    if a.only:
        datasets = [tuple(a.only.split(" "))]

    dest = Path(__file__).resolve().parents[3] / "reports" / "phase5_1" / "_e5_data.json"
    payload = {}
    if dest.exists():
        payload = json.loads(dest.read_text(encoding="utf-8"))

    settings = get_settings()
    for sym, tf in datasets:
        print(f"\n=== {sym} {tf} ===", flush=True)
        ctrl = run_variant(settings, sym, tf, h1=False)
        print(f"  control done n={len(ctrl)}", flush=True)
        h1 = run_variant(settings, sym, tf, h1=True)
        print(f"  h1 done n={len(h1)}", flush=True)
        for label, rows in (("CTRL", ctrl), ("H1", h1)):
            sd = score_distribution(rows)
            sb = setup_behavior(rows)
            print(f"  {label}: n={sd['n']} score min={sd['min']} med={sd['median']} mean={sd['mean']:.1f} max={sd['max']} "
                  f"b0_19={sd.get('bucket_0-19_pct')} b20_39={sd.get('bucket_20-39_pct')} "
                  f"b40_59={sd.get('bucket_40-59_pct')} b60_79={sd.get('bucket_60-79_pct')} b80={sd.get('bucket_80-100_pct')}",
                  flush=True)
            print(f"     {label}: entered={sb['entered']} expired={sb['expired']} invalidated={sb['invalidated']} never={sb['never_entered']}",
                  flush=True)

        def _clean(o):
            if isinstance(o, dict):
                return {k: _clean(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [_clean(v) for v in o]
            if isinstance(o, (np.integer, int)):
                return int(o)
            if isinstance(o, (np.floating, float)):
                if isinstance(o, float) and (math.isinf(o) or math.isnan(o)):
                    return None
                return float(o)
            return o

        payload[f"{sym} {tf}"] = {
            "control": _clean({
                "score_dist": score_distribution(ctrl),
                "buckets": bucket_outcomes(ctrl),
                "behavior": setup_behavior(ctrl),
                "scores_csv": ctrl.to_dict("records"),
            }),
            "h1": _clean({
                "score_dist": score_distribution(h1),
                "buckets": bucket_outcomes(h1),
                "behavior": setup_behavior(h1),
                "scores_csv": h1.to_dict("records"),
            }),
        }
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
        print(f"  wrote {dest}", flush=True)
    print("\nDone all requested datasets")