"""Stage-4 candidate validation: deep-walk-forward check before promotion.

Rules mirror what cleared BTC/ETH into ``_STAGE4_ALLOWED_TYPES``:

  1. Deep history is downloaded into the real candle store (additive only).
  2. One progressive dump records every detected setup with its component
     scores and realized 24h NET return (weight_search.dump_market).
  3. Offline 60/20/20 walk-forward freezes the Bayesian posterior gate on the
     TRAIN split, then measures eligible expectancy on VAL and OOS.
  4. An (setup_type, side) combo qualifies for the market allowlist only when
     it is eligible and positive on BOTH VAL and OOS with meaningful N.

Usage: python -m veyra.research.stage4_validate --symbol UNI/USDT --timeframe 4H
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from veyra.alerts.costs import CostModel
from veyra.data.candle_store import CandleStore
from veyra.data.provider import get_provider
from veyra.config import get_settings

from .weight_search import (  # noqa: PLC0415 - sibling research module reuse
    COMPONENTS,
    _eligible,
    _posterior_gate,
    dump_market,
    row_score,
    search,
    split_by_ts,
)

REPO_REPORTS = Path(__file__).resolve().parents[3] / "reports" / "weights"
MIN_TOTAL_N = 10
MIN_SPLIT_N = 4


def _qualifying_combos(rows, baseline):
    """Per (setup_type, side) allowlist decision under the frozen train gate."""
    train, val, test = split_by_ts(rows)
    gate = _posterior_gate(train, baseline)
    combos: dict = {}
    for row in rows:
        combos.setdefault((row["setup_type"], row["side"]), []).append(row)

    allowed = []
    for combo, members in sorted(combos.items()):
        val_el = [r for r in val if r["setup_type"] == combo[0] and r["side"] == combo[1]
                  and _eligible(r, baseline, gate)]
        test_el = [r for r in test if r["setup_type"] == combo[0] and r["side"] == combo[1]
                   and _eligible(r, baseline, gate)]
        v_rets = [float(r["net_24h"]) for r in val_el]
        t_rets = [float(r["net_24h"]) for r in test_el]
        v_exp = sum(v_rets) / len(v_rets) if v_rets else None
        t_exp = sum(t_rets) / len(t_rets) if t_rets else None
        entry = {
            "n_total": len(members),
            "n_val": len(v_rets), "n_oos": len(t_rets),
            "val_expectancy": round(v_exp, 5) if v_exp is not None else None,
            "oos_expectancy": round(t_exp, 5) if t_exp is not None else None,
            "val_win": (round(sum(1 for r in v_rets if r > 0) / len(v_rets), 3)
                        if v_rets else None),
            "oos_win": (round(sum(1 for r in t_rets if r > 0) / len(t_rets), 3)
                        if t_rets else None),
        }
        qualifies = (
            v_exp is not None and t_exp is not None
            and v_exp > 0 and t_exp > 0
            and len(v_rets) >= MIN_SPLIT_N and len(t_rets) >= MIN_SPLIT_N
            and (len(v_rets) + len(t_rets)) >= MIN_TOTAL_N
        )
        entry["qualifies"] = qualifies
        if qualifies:
            allowed.append(combo)
        combos[combo] = entry
    return combos, sorted(allowed)


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage-4 candidate validation.")
    parser.add_argument("--symbol", default="UNI/USDT")
    parser.add_argument("--timeframe", default="4H")
    args = parser.parse_args()

    settings = get_settings()
    store = CandleStore(settings)
    provider = get_provider("binance")

    print(f"stage4_validate — {args.symbol} {args.timeframe}")
    print("  phase 1: download deep history (additive into candle store)")
    # Full forward-paginated history from a historic start (UNI listed 2020-09).
    candles = provider.get_ohlcv(args.symbol, args.timeframe, start_time=1_598_918_400)
    if not candles:
        raise SystemExit("  no candles returned")
    store.write(args.symbol, args.timeframe, candles)
    df = store.load(args.symbol, args.timeframe)
    print(f"  stored {len(df)} bars")

    rows_path = REPO_REPORTS / f"_{args.symbol.replace('/', '_')}_{args.timeframe}_rows.jsonl"
    if rows_path.exists():
        rows_path.unlink()
    n = dump_market(args.symbol, args.timeframe, settings, rows_path)
    print(f"  dumped {n} setups")

    rows = []
    with rows_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    baseline = {c: getattr(settings, f"weight_{c.lower()}") for c in COMPONENTS}
    result = search(rows, baseline)
    combos, allowed = _qualifying_combos(rows, baseline)

    verdict = {
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "bars": len(df),
        "setups_dumped": len(rows),
        "baseline": baseline,
        "market": result,
        "qualified_combos": [" ".join(c) for c in allowed],
        "combo_details": {f"{k[0]} {k[1]}": v for k, v in combos.items()},
    }
    out = REPO_REPORTS / f"{args.symbol.replace('/', '_')}_{args.timeframe}_validation.json"
    out.write_text(json.dumps(verdict, indent=2, sort_keys=True), encoding="utf-8")

    b = result["baseline_result"]
    print("\n  WHOLE-MARKET walk-forward (posterior gate frozen on train)")
    print(f"    VAL : n={b['val']['n_eligible']} expectancy={b['val']['expectancy']} "
          f"WR={b['val']['win_rate']}")
    print(f"    OOS : n={b['test']['n_eligible']} expectancy={b['test']['expectancy']} "
          f"WR={b['test']['win_rate']}")
    best = result["best"]
    if best is not None and best["weights"] != baseline:
        print(f"    best OOS weights found: {best['weights']} "
              f"(OOS={best['test']['expectancy']:.5f})")

    print("\n  PER-TYPE/SIDE allowlist check (eligible under train-frozen gate)")
    for key in sorted(combos):
        c = combos[key]
        print(f"    {key[0]:24s} {key[1]:5s} nVal={c['n_val']:3d} "
              f"nOos={c['n_oos']:3d} val={c['val_expectancy']} "
              f"oos={c['oos_expectancy']} -> {'ALLOW' if c['qualifies'] else 'reject'}")

    print("\n  VERDICT: ", end="")
    if allowed:
        print(f"PROMOTE {args.symbol} {args.timeframe} with allowlist "
              f"{allowed}")
    else:
        print(f"DO NOT PROMOTE {args.symbol} {args.timeframe} "
              f"(no positive, gate-eligible combo on both VAL and OOS)")
    print(f"  details -> {out}")


if __name__ == "__main__":
    main()