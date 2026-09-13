import os
import sys
from pathlib import Path
from typing import Dict, List, Optional
import pandas as pd
from unittest.mock import patch

# Adjust path to import from veyra package
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from veyra.config import get_settings, Settings
from veyra.data.candle_store import CandleStore
from veyra.phase5.frozen_config import build_baseline_engine
from veyra.phase5.validate import FIXED_SCORE_BUCKETS, chronological_split
from veyra.backtest import MetricsEngine, BacktestRun, SimulationResult
from veyra.backtest.simulator import Simulator
from veyra.backtest.models import BacktestTrade
from veyra.backtest.metrics import MetricsResult


def _get_in_val_and_oos(run: SimulationResult, df: pd.DataFrame, metrics: MetricsEngine):
    split = chronological_split(len(df))
    times = df["open_time"].to_numpy()

    # IN / VAL combined
    in_val_start = int(times[split.training.start])
    in_val_end = int(times[split.validation.end - 1])
    in_val_trades = [
        t for t in run.trades if in_val_start <= int(t.entry_ts) <= in_val_end
    ]
    in_val_metrics = metrics.compute(in_val_trades, [], len(df), score_buckets=FIXED_SCORE_BUCKETS)

    # OOS
    oos_start = int(times[split.test.start])
    oos_end = int(times[split.test.end - 1])
    oos_trades = [
        t for t in run.trades if oos_start <= int(t.entry_ts) <= oos_end
    ]
    oos_metrics = metrics.compute(oos_trades, [], len(df), score_buckets=FIXED_SCORE_BUCKETS)

    return in_val_trades, in_val_metrics, oos_trades, oos_metrics


def summarize_metrics(label: str, trades: List[BacktestTrade], run_metrics: MetricsResult) -> dict:
    total = len(trades)
    if total == 0:
        return {}

    stops = sum(1 for t in trades if t.exit_reason == "STOP")
    targets = sum(1 for t in trades if t.exit_reason == "TARGET")
    expired = sum(1 for t in trades if t.exit_reason == "EXPIRED")

    stop_rate = stops / total * 100
    target_rate = targets / total * 100
    expired_rate = expired / total * 100

    avg_mae = sum(t.max_adverse_excursion for t in trades) / total
    avg_mfe = sum(t.max_favorable_excursion for t in trades) / total
    avg_return = sum(t.net_return for t in trades) / total

    return {
        "label": label,
        "trades": total,
        "win_rate": run_metrics.trades.win_rate * 100,
        "profit_factor": run_metrics.risk.profit_factor if run_metrics.risk.profit_factor else 0.0,
        "expectancy": run_metrics.risk.expectancy,
        "avg_return": avg_return,
        "avg_holding": run_metrics.risk.average_holding_bars,
        "stop_rate": stop_rate,
        "target_rate": target_rate,
        "expired_rate": expired_rate,
        "max_drawdown": run_metrics.risk.max_drawdown,
        "avg_mae": avg_mae,
        "avg_mfe": avg_mfe,
    }


def run_e1_experiment(symbol="BTC/USDT", timeframe="1D"):
    settings = get_settings()
    store = CandleStore(settings)
    df = store.load(symbol, timeframe)
    df = df.sort_values("open_time").reset_index(drop=True)
    
    print(f"\n--- Running E1 on {symbol} {timeframe} ---")

    metrics_engine = MetricsEngine()

    # 1. Baseline
    engine_baseline = build_baseline_engine(settings, progressive=True)
    run_baseline = engine_baseline.run(symbol, timeframe, df.copy(), run_key="baseline")
    bl_in_val_trades, bl_in_val, bl_oos_trades, bl_oos = _get_in_val_and_oos(run_baseline, df, metrics_engine)
    
    # 2. E1a: Extended Lifespan (60 -> 120 bars)
    engine_e1a = build_baseline_engine(settings, progressive=True)
    engine_e1a._execution_overrides = {"position_max_bars": 120}
    run_e1a = engine_e1a.run(symbol, timeframe, df.copy(), run_key="e1a")
    e1a_in_val_trades, e1a_in_val, e1a_oos_trades, e1a_oos = _get_in_val_and_oos(run_e1a, df, metrics_engine)
    
    # 3. E1b: Reduced Targets (target distance reduced by 50%)
    engine_e1b = build_baseline_engine(settings, progressive=True)
    
    orig_enter = Simulator.enter
    def mocked_enter(self, key, raw_open, ts, bar_index, normalizer):
        # We need to intercept the position right after it's created, but wait: 
        # enter() computes target, creates OpenPosition and stores it.
        # It's easier to call orig_enter, then modify the position it created!
        pos_key = orig_enter(self, key, raw_open, ts, bar_index, normalizer)
        if pos_key:
            pos = self._positions[pos_key]
            if pos.target is not None:
                # Reduce target distance from entry by 50%
                distance = pos.target - raw_open
                pos.target = raw_open + distance * 0.5
        return pos_key

    with patch.object(Simulator, 'enter', new=mocked_enter):
        run_e1b = engine_e1b.run(symbol, timeframe, df.copy(), run_key="e1b")
    e1b_in_val_trades, e1b_in_val, e1b_oos_trades, e1b_oos = _get_in_val_and_oos(run_e1b, df, metrics_engine)
    
    # Compare IN/VAL
    def format_row(m):
        if not m:
            return ""
        pf_str = f"{m['profit_factor']:.2f}" if m['profit_factor'] != float('inf') else "inf "
        return (
            f"{m['label']:<12} | {m['trades']:<6} | {m['expired_rate']:>5.1f}% | "
            f"{m['stop_rate']:>5.1f}% | {m['target_rate']:>5.1f}% | "
            f"{m['expectancy']:>8.4f} | {m['avg_return']:>8.4f} | "
            f"{pf_str:>5} | {m['win_rate']:>5.1f}% | {m['avg_holding']:>5.1f} | "
            f"{m['avg_mae']:>8.4f} | {m['avg_mfe']:>8.4f} | {m['max_drawdown']:.4f}"
        )

    header = (
        f"{'Label':<12} | {'Trades':<6} | {'Exp%':>6} | {'Stop%':>6} | "
        f"{'Tgt%':>6} | {'Expect':>8} | {'AvgRet':>8} | "
        f"{'PF':>5} | {'Win%':>6} | {'Hold':>5} | "
        f"{'AvgMAE':>8} | {'AvgMFE':>8} | Drawdown"
    )
    sep = "-" * 115

    print("\n[IN/VAL COMPARISON]")
    print(header)
    print(sep)
    print(format_row(summarize_metrics("Baseline", bl_in_val_trades, bl_in_val)))
    print(format_row(summarize_metrics("E1a (120b)", e1a_in_val_trades, e1a_in_val)))
    print(format_row(summarize_metrics("E1b (Tgt50%)", e1b_in_val_trades, e1b_in_val)))

    print("\n[OOS — reference only, NOT used to select or optimise experiments]")
    print(header)
    print(sep)
    print(format_row(summarize_metrics("Baseline", bl_oos_trades, bl_oos)))
    print(format_row(summarize_metrics("E1a (120b)", e1a_oos_trades, e1a_oos)))
    print(format_row(summarize_metrics("E1b (Tgt50%)", e1b_oos_trades, e1b_oos)))

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--fast", action="store_true", help="Run only 1D")
    args = parser.parse_args()
    
    run_e1_experiment("BTC/USDT", "1D")
    run_e1_experiment("ETH/USDT", "1D")
    if not args.fast:
        run_e1_experiment("BTC/USDT", "4H")
        run_e1_experiment("ETH/USDT", "4H")
