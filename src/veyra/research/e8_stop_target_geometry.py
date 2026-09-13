"""E8 — Stop/Target-Geometry Attribution (diagnostic only).

Central question:
    Veyra's dominant mechanical damage (E7) is the STOP and the absent/too-close
    TARGET geometry. E8 measures the GEOMETRY of that damage — purely
    descriptively — to size a FUTURE controlled conditional-geometry experiment.
    E8 changes NOTHING: no production, baseline, param, detector, or exit change;
    no re-simulation; no threshold invention; no VAL/OOS tuning. It reuses the
    frozen E7 realized-path data (the identifier of the realized stop/target run)
    and the raw OHLC path to quantify geometry statements.

E8 answers (all descriptive facts about the already-recorded realized path):
  G1  Favorable-first STOP depth: among trades that moved FAVORABLE first and were
      still stopped, how far past their favorable peak did price have to travel to
      reach the (frozen) stop? Concretely: (a) peak favorable excursion MFE% at
      bars_to_mfe; (b) adverse depth MAE% relative to that peak = MAE% - MFE%;
      (c) how many bars separated the favorable peak from the stop bar. This
      quantifies WHERE a wider stop would sit and how much early wiggle it absorbs.
  G2  No-target reachable distance: BREAKOUT / BREAKOUT_RETEST / RANGE_REJECTION
      carry no usable target. Measure the ACTUAL maximal favorable excursion
      reached during the realized hold (MFE%) to establish what target distance
      was realistically reachable, and the fraction of these trades that would
      have been profitable had a target been reachable at the realized MFE.
  G3  RR separation: distribution of realized reward:risk (rew_risk) for trades
      WITH a target, and the realized net return by rew_risk bucket — to see
      whether the frozen RR itself distinguishes winners (descriptive, forward
      label; NOT a threshold recommendation).
  G4  Time geometry: distribution of bars_to_mfe for stopped trades (how far the
      favorable peak sits from entry in bars), and bars_to_mfe vs bars_to_mae for
      favorable-first stopped trades, to inform stop width in time.

Outputs are written to reports/phase5_1/_e8_data.json (per-trade geometry facts
appended from the E7 record + path-derived depth) and a printed table.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from veyra.config import get_settings
from veyra.data.candle_store import CandleStore
from veyra.phase5.frozen_config import PHASE5_BASELINE_VERSION

E7_DATA = Path(__file__).resolve().parents[3] / "reports" / "phase5_1" / "_e7_data.json"
DEST = Path(__file__).resolve().parents[3] / "reports" / "phase5_1" / "_e8_data.json"

NO_TARGET_FAMILIES = ("BREAKOUT", "RANGE_REJECTION")
RETEST_PARTIAL_TARGET = ("BREAKOUT_RETEST",)  # ~52% no target


def _pct(arr, q):
    if not arr:
        return None
    s = sorted(arr)
    k = max(0, min(len(s) - 1, int(round((len(s) - 1) * q))))
    return s[k]


def _mean(arr):
    return sum(arr) / len(arr) if arr else None


def trade_geometry(rec, df):
    """From the E7 record + raw OHLC path, compute E8 geometry facts.
    rec: one E7 per-trade record (has entry_bar/exit_bar/bars_to_mfe/bars_to_mae/
         fwd_first/reference stop/target). All path metrics are realized-path labels.
    """
    ei = rec.get("entry_bar")
    xi = rec.get("exit_bar")
    side = rec.get("side")
    ref = rec.get("entry_ref")
    g = {"trade_id": rec.get("trade_id"), "setup_type": rec.get("setup_type"),
         "side": side, "period": rec.get("period"), "exit_reason": rec.get("exit_reason"),
         "net_return": rec.get("net_return"), "holding_bars": rec.get("holding_bars")}
    if ei is None or xi is None or ref is None or ref <= 0 or ei >= len(df) or xi >= len(df):
        g["incomplete"] = True
        return g
    n = len(df)
    hi = df["high"].to_numpy(); lo = df["low"].to_numpy()
    # realized-path extremes
    mfe = 0.0; mae = 0.0; mfe_bar = ei; mae_bar = ei
    for b in range(ei, min(xi + 1, n)):
        f = (hi[b] - ref) / ref if side == "LONG" else (ref - lo[b]) / ref
        a = (ref - lo[b]) / ref if side == "LONG" else (hi[b] - ref) / ref
        if f > mfe: mfe = f; mfe_bar = b
        if a > mae: mae = a; mae_bar = b
    g["mfe_pct"] = mfe
    g["mae_pct"] = mae
    g["bars_to_mfe"] = rec.get("bars_to_mfe")
    g["bars_to_mae"] = rec.get("bars_to_mae")
    g["fwd_first"] = rec.get("fwd_first")
    # G1: for favorable-first STOP, adverse depth PAST the favorable peak
    sq = (mfe + mae)  # (mfe + mae) = round-trip from peak to trough on favorable-first
    g["peak_to_trough_pct"] = mfe + mae if (mfe > 0 and mae > 0) else None
    # bars between favorable peak and the MAE extreme (worst inside hold)
    g["bars_peak_to_trough"] = abs(mfe_bar - mae_bar) if mfe_bar != ei and mae_bar != ei else None
    g["stop_dist_pct"] = rec.get("stop_dist_pct")
    g["target_dist_pct"] = rec.get("target_dist_pct")
    g["rew_risk"] = rec.get("rew_risk")
    g["target"] = rec.get("target")
    g["stop"] = rec.get("stop")
    return g


def analyze_rows(df, rows):
    res = defaultdict(list)
    for r in rows:
        if r.get("incomplete"):
            continue
        res["all"].append(r)
        res[f"exit:{r['exit_reason']}"].append(r)
    out = {}
    for grp, lst in res.items():
        rr = [x["rew_risk"] for x in lst if x.get("rew_risk") is not None]
        mfe = [x["mfe_pct"] for x in lst if x.get("mfe_pct") is not None]
        mae = [x["mae_pct"] for x in lst if x.get("mae_pct") is not None]
        ptt = [x["peak_to_trough_pct"] for x in lst if x.get("peak_to_trough_pct") is not None]
        bpt = [x["bars_peak_to_trough"] for x in lst if x.get("bars_peak_to_trough") is not None]
        bt_mfe = [x["bars_to_mfe"] for x in lst if x.get("bars_to_mfe") is not None]
        out[grp] = {
            "n": len(lst),
            "mfe_pct": [_mean(mfe), _pct(mfe, 0.25), _pct(mfe, 0.5), _pct(mfe, 0.75)],
            "mae_pct": [_mean(mae), _pct(mae, 0.5)],
            "peak_to_trough_pct": [_mean(ptt), _pct(ptt, 0.5), _pct(ptt, 0.75)],
            "bars_peak_to_trough": [_mean(bpt) if bpt else None, _pct(bpt, 0.5)],
            "bars_to_mfe": [_mean(bt_mfe) if bt_mfe else None, _pct(bt_mfe, 0.5)],
            "rew_risk": [_mean(rr), _pct(rr, 0.25), _pct(rr, 0.5), _pct(rr, 0.75)],
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="e.g. 'BTC/USDT 4H'")
    a = ap.parse_args()
    settings = get_settings()
    e7 = json.loads(E7_DATA.read_text(encoding="utf-8"))
    datasets = [k for k in e7 if k != "meta"]
    if a.only:
        datasets = [a.only]

    payload = {"meta": {"baseline": PHASE5_BASELINE_VERSION,
                        "source": "e7_exit_mechanics", "notes": "diagnostic geometry labels only; no threshold or decision"}}
    store = CandleStore(settings)
    for sym_tf in datasets:
        sym, tf = sym_tf.rsplit(" ", 1)
        df = store.load(sym, tf).sort_values("open_time").reset_index(drop=True)
        rows = [trade_geometry(r, df) for r in e7[sym_tf]]
        payload[sym_tf] = rows
        print("=" * 78)
        print(f"{sym_tf}  trades={len(rows)}")
        for grp in ["all", "exit:STOP", "exit:TARGET", "exit:EXPIRED"]:
            o = analyze_rows(df, rows)[grp]
            if not o.get("n"):
                continue
            def f5(x):
                y = x[0]
                return round(y, 4) if isinstance(y, float) else y
            print(f"  [{grp}] n={o['n']:<5} MFE%(mean/p25/p50/p75)={[round(z,4) if isinstance(z,float) else z for z in o['mfe_pct']]}"
                  f"  MAE% med={round(o['mae_pct'][1],4) if isinstance(o['mae_pct'][1],float) else None}"
                  f"  pkt->trough(mean/p50)={[round(z,4) if isinstance(z,float) else z for z in o['peak_to_trough_pct'][:2]]}"
                  f"  bars_pk->trough(m/p50)={[round(z,2) if isinstance(z,float) else z for z in o['bars_peak_to_trough'][:2]]}"
                  f"  REW/RISK(m/p25/p50/p75)={[round(z,2) if isinstance(z,float) else z for z in o['rew_risk']] if isinstance(o['rew_risk'][0],float) or o['rew_risk'][0] is None else o['rew_risk']}")
        # G2: no-target families realised MFE (reachable target distance)
        nt = [r for r in rows if r.get("setup_type") in NO_TARGET_FAMILIES or
              (r.get("setup_type") in RETEST_PARTIAL_TARGET and r.get("target") is None)]
        if nt:
            mfe_nt = [r["mfe_pct"] for r in nt if r.get("mfe_pct") is not None]
            print(f"  G2 no-target n={len(nt)}  realised MFE%(mean/p50/p75)="
                  f"{[round(z,4) if isinstance(z,float) else z for z in [_mean(mfe_nt), _pct(mfe_nt,0.5), _pct(mfe_nt,0.75)]]}")
        DEST.parent.mkdir(parents=True, exist_ok=True)
        DEST.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print("\nWrote", DEST)


if __name__ == "__main__":
    main()