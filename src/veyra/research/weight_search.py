"""Quarterly walk-forward re-validation of the hand-tuned component weights.

The 0-100 setup score is ``overall = round(Σ wᵢ·cᵢ / Σ wᵢ)`` with hand-tuned
weights (trend 0.25, structure 0.20, pullback 0.15, momentum 0.12, volume
0.07, volatility 0.05). This script re-validates that choice over real history:

PHASE 1 (dump, once per market): one progressive ``analyze_each`` pass per
dataset records every candidate setup with its per-component scores and its
realized 24h net return — the full matrix a reweight needs, WITHOUT re-running
a backtest per weight candidate.

PHASE 2 (offline): walk-forward 60/20/20 train/val/test. Each candidate weight
set recomputes the canonical scores by pure arithmetic, freezes the Bayesian
posterior gate on the training split, then measures out-of-sample expectancy on
VAL and OOS with a bootstrap confidence interval. A candidate whose CI crosses
zero is flagged and excluded.

Output: reports/weights/weight_result.json + printed comparison.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from veyra.alerts.costs import CostModel
from veyra.alerts.forward import ForwardReturnModel, horizon_for
from veyra.alerts.models import AlertLevel
from veyra.config import get_settings
from veyra.data.candle_store import CandleStore
from veyra.domain.setup import Setup
from veyra.market.pipeline import MarketAnalysisPipeline
from veyra.paper.quality import MIN_POSTERIOR_WIN_RATE, posterior_stats
from veyra.strategy.setup_engine import SetupEngine

REPO_REPORTS = Path(__file__).resolve().parents[3] / "reports" / "weights"
COMPONENTS = ["TREND", "STRUCTURE", "PULLBACK", "MOMENTUM", "VOLUME", "VOLATILITY"]
STEP = 0.05


# ── Phase 1: detection matrix ──────────────────────────────────────────────

def dump_market(symbol: str, timeframe: str, settings, rows_path: Path) -> int:
    """One progressive analysis pass -> one JSONL row per detected setup.

    Each row carries the per-component scores (exact inputs to the aggregator)
    and the realized net 24h return after the detection bar, graded the same way
    the band evidence / posterior gate consumes it.
    """
    store = CandleStore(settings)
    df = store.load(symbol, timeframe)
    if df is None or df.empty:
        return 0
    df = df.sort_values("open_time").reset_index(drop=True)
    snapshots = MarketAnalysisPipeline.default(settings).analyze_each(symbol, timeframe, df)
    engine = SetupEngine(settings)
    horizon = horizon_for(timeframe)
    forward = ForwardReturnModel(df, horizon=horizon)
    cost = CostModel()

    n = 0
    rows_path.parent.mkdir(parents=True, exist_ok=True)
    with rows_path.open("a", encoding="utf-8") as handle:
        for snap in snapshots:
            for setup in engine.detect(snap):
                assert isinstance(setup, Setup)
                entry = None
                area = setup.interest_area
                if area is not None:
                    lo = getattr(area, "low", None)
                    hi = getattr(area, "high", None)
                    entry = hi if hi is not None else lo
                realized = forward.realized(
                    detection_ts=int(setup.timestamp),
                    side=setup.side.value,
                    entry_price=float(entry) if entry is not None else None,
                    horizon=horizon,
                )
                if realized is None:
                    continue
                row = {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "ts": int(setup.timestamp),
                    "setup_type": setup.setup_type.value,
                    "side": setup.side.value,
                    "regime": setup.regime.value,
                    "scores": {name.value: setup.scores.get(name, 0) for name in setup.scores},
                    "net_24h": float(realized) - cost.round_trip,
                    "score": int(setup.overall_score),
                }
                handle.write(json.dumps(row) + "\n")
                n += 1
    return n


def dump_all(markets: List[Dict[str, str]], settings) -> Path:
    rows_path = REPO_REPORTS / "_weight_rows.jsonl"
    if rows_path.exists():
        rows_path.unlink()
    for ds in markets:
        n = dump_market(ds["symbol"], ds["timeframe"], settings, rows_path)
        print(f"  dumped {n:6d} setups  {ds['symbol']} {ds['timeframe']}")
    return rows_path


# ── Offline re-validation ───────────────────────────────────────────────────

def row_score(row: dict, weights: Dict[str, float]) -> int:
    """Canonical weighted score for a dumped row, offline."""
    scores = row.get("scores", {})
    numer = sum(weights.get(c, 0.0) * float(scores.get(c, 0)) for c in COMPONENTS)
    denom = sum(weights.get(c, 0.0) for c in COMPONENTS)
    if denom <= 0:
        return 0
    return int(round(numer / denom))


def band_for(score: int) -> str:
    return AlertLevel.for_score(score).name


def _posterior_gate(train: List[dict], weights: Dict[str, float]) -> dict:
    """Freeze per-band posterior stats on the training set (spec-parity w/ paper gate).

    Returns {band: {"n": k, "wins": w, "mean_return": m, "risk": r}}.
    """
    by_band: Dict[str, List[float]] = {}
    for row in train:
        band = band_for(row_score(row, weights))
        if band == "SCANNED":
            continue
        by_band.setdefault(band, []).append(float(row["net_24h"]))
    gate = {}
    for band, rets in by_band.items():
        n = len(rets)
        wins = sum(1 for r in rets if r > 0)
        mean = sum(rets) / n
        losses = [r for r in rets if r < 0]
        risk = (sum(losses) / len(losses)) if losses else None
        gate[band] = {"n": n, "wins": wins, "mean_return": mean, "risk": risk}
    return gate


def _eligible(row: dict, weights: Dict[str, float], gate: dict) -> bool:
    band = band_for(row_score(row, weights))
    if band == "SCANNED" or band not in gate:
        return False
    st = type("S", (), dict(**gate[band], win_rate=gate[band]["wins"] / gate[band]["n"]))()
    pm, pw, n = posterior_stats(st)
    if pm <= 0:
        return False
    if pw < MIN_POSTERIOR_WIN_RATE:
        return False
    risk = gate[band].get("risk")
    if n > 0 and risk is not None and pm <= risk:
        return False
    return True


def evaluate(rows: List[dict], weights: Dict[str, float], gate: dict) -> dict:
    eligible = [r for r in rows if _eligible(r, weights, gate)]
    rets = [float(r["net_24h"]) for r in eligible]
    if not rets:
        return {"n_eligible": 0, "expectancy": None, "win_rate": None, "mean_score": None}
    return {
        "n_eligible": len(rets),
        "expectancy": sum(rets) / len(rets),
        "win_rate": sum(1 for r in rets if r > 0) / len(rets),
        "mean_score": sum(row_score(r, weights) for r in eligible) / len(eligible),
    }


def bootstrap_ci(rets: List[float], seeds: int = 200, alpha: float = 0.05) -> dict:
    import random

    if not rets:
        return {"lo": None, "hi": None}
    means = []
    rng = random.Random(7)
    for _ in range(seeds):
        sample = [rets[rng.randrange(len(rets))] for _ in rets]
        means.append(sum(sample) / len(sample))
    means.sort()
    lo = means[int(alpha / 2 * len(means))]
    hi = means[int((1 - alpha / 2) * len(means)) - 1]
    return {"lo": lo, "hi": hi}


def _weight_combos(baseline: Dict[str, float]) -> List[Dict[str, float]]:
    """Deterministic lightweight grid: perturb one component at a time by ±STEP,
    plus a full positive/equal sweep, always sum-normalized to the baseline sum."""
    base_sum = sum(baseline.values())
    combos: List[Dict[str, float]] = {}
    combo_rows = [dict(baseline)]
    for c in COMPONENTS:
        for delta in (-STEP, STEP):

            def row(base=baseline, comp=c, d=delta):
                out = dict(base)
                out[comp] = base[comp] + d
                return out

            combo_rows.append(row())
    for row in combo_rows:
        total = sum(row.values())
        if abs(total - base_sum) < 1e-12:
            norm = dict(row)  # exact baseline identity preserved
        else:
            scale = base_sum / total
            norm = {k: v * scale for k, v in row.items()}
        combos[json.dumps(sorted(norm.items()), sort_keys=True)] = norm
    return list(combos.values())


def split_by_ts(rows: List[dict]) -> tuple:
    rows = sorted(rows, key=lambda r: r["ts"])
    n = len(rows)
    cut1 = int(n * 0.60)
    cut2 = int(n * 0.80)
    return rows[:cut1], rows[cut1:cut2], rows[cut2:]


def search(rows: List[dict], baseline: Dict[str, float]) -> dict:
    train, val, test = split_by_ts(rows)
    results = []
    for weights in _weight_combos(baseline):
        gate = _posterior_gate(train, weights)
        val_res = evaluate(val, weights, gate)
        test_res = evaluate(test, weights, gate)
        ci = (bootstrap_ci(
            [float(r["net_24h"]) for r in val if _eligible(r, weights, gate)]
        ) if val_res["n_eligible"] else {"lo": None, "hi": None})
        results.append({
            "weights": weights,
            "val": val_res,
            "test": test_res,
            "val_ci": ci,
        })
    usable = [r for r in results
              if r["val"]["n_eligible"] and r["test"]["n_eligible"]
              and r["val_ci"]["lo"] is not None and r["val_ci"]["lo"] > 0]
    baseline_res = next(r for r in results if r["weights"] == baseline)
    best = (
        max(usable, key=lambda r: r["test"]["expectancy"])
        if usable else None
    )
    return {
        "baseline": baseline, "baseline_result": baseline_res,
        "best": best, "n_candidates": len(results),
        "rows_dumped": len(rows),
        "flagged_ci_crosses_zero": len([r for r in results
                                        if r["val"]["n_eligible"]
                                        and r["val_ci"]["lo"] is not None
                                        and r["val_ci"]["lo"] <= 0]),
    }


def main(markets: Optional[List[str]] = None) -> None:
    settings = get_settings()
    default = [
        {"symbol": "BTC/USDT", "timeframe": "4H"},
        {"symbol": "ETH/USDT", "timeframe": "4H"},
        {"symbol": "SOL/USDT", "timeframe": "4H"},
    ]
    if markets:
        pairs = []
        for entry in markets:
            symbol, timeframe = entry.split(":")
            pairs.append({"symbol": symbol, "timeframe": timeframe})
        markets = pairs
    else:
        markets = default

    print("weight_search — phase 1: detection matrix dump")
    rows_path = dump_all(markets, settings)
    rows = []
    with rows_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    print(f"  total dumped rows: {len(rows)}")

    baseline = {c: getattr(settings, f"weight_{c.lower()}") for c in COMPONENTS}
    result = search(rows, baseline)

    REPO_REPORTS.mkdir(parents=True, exist_ok=True)
    (REPO_REPORTS / "weight_result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True), encoding="utf-8"
    )
    _print_result(result)


def _print_result(result: dict) -> None:
    print("\nweight_search — phase 2: walk-forward re-validation")
    b = result["baseline_result"]
    oos_txt = ("n/a" if b["test"]["expectancy"] is None
               else f"{b['test']['expectancy']:.5f}")
    print(f"  baseline weights : {result['baseline']}  "
          f"VAL expectancy={b['val']['expectancy']:.5f} "
          f"(n={b['val']['n_eligible']})  OOS={oos_txt}")
    print(f"  candidates tested: {result['n_candidates']}  "
          f"flagged CI<=0: {result['flagged_ci_crosses_zero']}")
    best = result["best"]
    if best is None:
        print("  NO candidate beat baseline with a CI > 0 — hand-tuned weights stand.")
    else:
        print(f"  BEST OOS weights   : {best['weights']}")
        print(f"    VAL expectancy   : {best['val']['expectancy']:.5f} "
              f"(n={best['val']['n_eligible']}, CI={best['val_ci']})")
        print(f"    OOS expectancy   : {best['test']['expectancy']:.5f} "
              f"(n={best['test']['n_eligible']})")
        if best["weights"] != result["baseline"]:
            print("  SUGGESTED config change (quarterly review): CANDIDATE BEATS BASELINE")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Quarterly weight re-validation.")
    parser.add_argument(
        "--markets", nargs="*", default=None,
        help="Market windows, e.g. BTC/USDT:4H SOL/USDT:1D (default: BTC/ETH/SOL 4H)",
    )
    args = parser.parse_args()
    main(args.markets)