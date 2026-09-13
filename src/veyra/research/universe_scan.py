"""Scan a broad candidate universe for setup frequency + crude expectancy.

Downloads a bounded recent window per (symbol, tf) into an ISOLATED temp
store, then replays the exact detection chain the paper engine uses
(progressive analyze_each + SetupEngine.detect) and grades each detected
setup by its realized 24h NET return (ForwardReturnModel - round-trip cost).

Output: reports/universe_scan.json + printed ranking table.
"""

import json
import time
from pathlib import Path
from typing import Dict

from veyra.alerts.costs import CostModel
from veyra.alerts.forward import ForwardReturnModel, horizon_for
from veyra.alerts.models import AlertLevel
from veyra.config import get_settings
from veyra.data.candle_store import CandleStore
from veyra.data.provider import get_provider
from veyra.domain.setup import Setup
from veyra.market.pipeline import MarketAnalysisPipeline
from veyra.strategy.setup_engine import SetupEngine

BASE = Path(__file__).resolve().parents[2]
REPORT = BASE / "reports" / "universe_scan.json"
TEMP_STORE = BASE / "reports" / "_scanstore"

WINDOW_BARS = {"1H": 2000, "4H": 1600}
SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT",      # baselines (stage 4)
    "ADA/USDT", "XRP/USDT", "DOGE/USDT", "DOT/USDT", "AVAX/USDT",
    "LINK/USDT", "LTC/USDT", "UNI/USDT", "ATOM/USDT", "NEAR/USDT",
    "INJ/USDT", "OP/USDT", "ARB/USDT", "TON/USDT", "MATIC/USDT",
]
TIMEFRAMES = ["1H", "4H"]


class _StubSettings:
    @property
    def absolute_candle_store_dir(self):
        return TEMP_STORE


def _fetch(provider, store, symbol, timeframe):
    now_s = int(time.time())
    horizon = horizon_for(timeframe)
    interval_s = horizon // 24   # 1H -> 3600, 4H -> 14400
    window_s = WINDOW_BARS[timeframe] * interval_s
    candles = provider.get_ohlcv(
        symbol, timeframe, start_time=now_s - window_s, end_time=now_s
    )
    if not candles:
        return None
    store.write(symbol, timeframe, candles)
    df = store.load(symbol, timeframe)
    if df.empty:
        return None
    return df.sort_values("open_time").reset_index(drop=True)


def _harvest(settings, pipeline, engine, forward, cost, symbol, timeframe, df):
    snapshots = pipeline.analyze_each(symbol, timeframe, df)
    rows = []
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
                horizon=horizon_for(timeframe),
            )
            if realized is None:
                continue
            rows.append({
                "ts": int(setup.timestamp),
                "setup_type": setup.setup_type.value,
                "side": setup.side.value,
                "regime": setup.regime.value,
                "score": int(setup.overall_score),
                "band": AlertLevel.for_score(int(setup.overall_score)).name,
                "net_24h": float(realized) - cost.round_trip,
            })
    return rows


def main() -> None:
    settings = get_settings()
    provider = get_provider("binance")
    store = CandleStore(_StubSettings())
    pipeline = MarketAnalysisPipeline.default(settings)
    engine = SetupEngine(settings)
    cost = CostModel()

    out: Dict[str, dict] = {}
    for tf in TIMEFRAMES:
        for symbol in SYMBOLS:
            key = f"{symbol} {tf}"
            try:
                df = _fetch(provider, store, symbol, tf)
                if df is None or len(df) < 200:
                    print(f"SKIP  {key}: no/too-few candles ({0 if df is None else len(df)})")
                    continue
                n = len(df)
                forward = ForwardReturnModel(df, horizon=horizon_for(tf))
                rows = _harvest(settings, pipeline, engine, forward, cost, symbol, tf, df)
                n_setups = len(rows)
                if not rows:
                    print(f"EMPTY {key}: n={n} setups=0")
                    out[key] = {"symbol": symbol, "timeframe": tf, "bars": n,
                                "setups": 0, "setups_per_100": 0.0, "mean_score": None,
                                "convergent": 0, "avg_net24h": None, "win_rate": None}
                    continue
                rets = [r["net_24h"] for r in rows]
                conv = [r for r in rows if r["band"] == "CONVERGENT"]
                conv_rets = [r["net_24h"] for r in conv]
                avg_conv = sum(conv_rets) / len(conv_rets) if conv_rets else None
                n_win = sum(1 for r in rets if r > 0)
                out[key] = {
                    "symbol": symbol, "timeframe": tf, "bars": n,
                    "setups": n_setups,
                    "setups_per_100": round(100.0 * n_setups / n, 2),
                    "mean_score": round(sum(r["score"] for r in rows) / n_setups, 1),
                    "convergent": len(conv),
                    "avg_net24h": round(sum(rets) / len(rets), 5),
                    "avg_conv_net24h": round(avg_conv, 5) if avg_conv is not None else None,
                    "win_rate": round(n_win / n_setups, 3),
                }
                print(f"OK    {key}: bars={n:5d} setups={n_setups:4d} "
                      f"per100={out[key]['setups_per_100']:5.1f} "
                      f"conv={len(conv):3d} avgNet={out[key]['avg_net24h']:+.4f} "
                      f"convNet={out[key]['avg_conv_net24h'] if out[key]['avg_conv_net24h'] is not None else '--'}")
            except Exception as exc:
                print(f"ERR   {key}: {type(exc).__name__}: {exc}")
                out[key] = {"symbol": symbol, "timeframe": tf, "error": str(exc)}

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nwrote {REPORT}")

    print("\nRANKING by setups per 100 bars (markets with setups only):")
    rows = [v for v in out.values() if v.get("setups", 0) > 0]
    rows.sort(key=lambda r: -r.get("setups_per_100", 0))
    print(f"{'market':20s} {'tf':3s} {'bars':>5s} {'setups':>6s} {'per100':>6s} "
          f"{'conv':>4s} {'meanSc':>6s} {'avgNet24h':>9s} {'convNet':>8s} {'WR':>5s}")
    for r in rows:
        print(f"{r['symbol']:20s} {r['timeframe']:3s} {r['bars']:5d} {r['setups']:6d} "
              f"{r['setups_per_100']:6.1f} {r['convergent']:4d} "
              f"{(r['mean_score'] or 0.0):6.1f} "
              f"{(r['avg_net24h'] or 0.0):+9.4f} "
              f"{(r['avg_conv_net24h'] or 0.0):+8.4f} "
              f"{(r['win_rate'] or 0.0):5.2f}")


if __name__ == "__main__":
    main()