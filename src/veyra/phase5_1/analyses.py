"""Phase 5.1 diagnostic analyses: compute breakdowns over the frozen results.

Each analyser takes a ``DiagnosticsSet`` (raw per-dataset trades/setups) and
returns plain, reportable structures. The baseline and data are not modified.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..backtest import BacktestTrade, SetupRecord
from .diagnostics import (
    CONFIGURED_REGIMES,
    DEFAULT_DATASETS_SPEC,
    SCORE_BUCKETS,
    SETUP_TYPES,
    TIME_FRAMES,
    DatasetData,
    DiagnosticsSet,
    SliceStats,
    count_outcomes,
    slice_stats,
)


def by_market(all_trades: List[BacktestTrade]) -> Dict[str, SliceStats]:
    """Aggregate across all datasets (pool) + split by asset and timeframe."""
    return {
        "ALL": slice_stats(all_trades, len(all_trades), "ALL"),
        "BTC": slice_stats([t for t in all_trades if t.symbol == "BTC/USDT"], 0, "BTC"),
        "ETH": slice_stats([t for t in all_trades if t.symbol == "ETH/USDT"], 0, "ETH"),
        "4H": slice_stats([t for t in all_trades if t.timeframe == "4H"], 0, "4H"),
        "1D": slice_stats([t for t in all_trades if t.timeframe == "1D"], 0, "1D"),
    }


def by_setup_type(all_trades: List[BacktestTrade], all_setups: List[SetupRecord]) -> Dict[str, SliceStats]:
    rows: Dict[str, SliceStats] = {}
    for st in SETUP_TYPES:
        trades = [t for t in all_trades if t.setup_type == st]
        detected = sum(1 for s in all_setups if s.setup_type == st)
        rows[st] = slice_stats(trades, detected, st)
    return rows


def setups_funnel(all_setups: List[SetupRecord]) -> Dict[str, int]:
    """Detection funnel per setup type: detected -> qualified -> triggered."""
    out: Dict[str, Dict[str, int]] = {}
    for st in SETUP_TYPES:
        group = [s for s in all_setups if s.setup_type == st]
        out[st] = {
            "detected": len(group),
            "qualified": sum(
                1 for s in group if s.outcome in ("QUALIFIED_NO_TRADE", "COMPLETED", "EXPIRED", "INVALIDATED")
            ),
            "triggered": sum(1 for s in group if s.outcome == "COMPLETED"),
        }
    return out


def score_bucket_analysis(all_trades: List[BacktestTrade]) -> Dict[str, SliceStats]:
    """Fixed, result-independent score buckets (normalized 0-100)."""
    rows: Dict[str, SliceStats] = {}
    for label in SCORE_BUCKETS:
        lo, hi = (int(label.split("-")[0]), int(label.split("-")[1]))
        trades = [t for t in all_trades if lo <= t.score_normalized <= hi]
        rows[label] = slice_stats(trades, len(trades), label)
    return rows


def regime_analysis(all_trades: List[BacktestTrade]) -> Dict[str, SliceStats]:
    rows: Dict[str, SliceStats] = {}
    regimes = set(t.regime for t in all_trades)
    for reg in sorted(regimes):
        trades = [t for t in all_trades if t.regime == reg]
        rows[reg] = slice_stats(trades, len(trades), reg)
    return rows


def asset_timeframe_matrix(set: DiagnosticsSet) -> Dict[str, SliceStats]:
    """4 corner cells: BTC 4H, BTC 1D, ETH 4H, ETH 1D."""
    rows: Dict[str, SliceStats] = {}
    for sym, tf in DEFAULT_DATASETS_SPEC:
        trades = [
            t for d in set.datasets if d.symbol == sym and d.timeframe == tf for t in d.trades
        ]
        label = f"{sym} {tf}"
        rows[label] = slice_stats(trades, len(trades), label)
    return rows


def setup_by_grid(
    all_trades: List[BacktestTrade], grid: str
) -> Dict[str, Dict[str, SliceStats]]:
    """Crosstab of setup type by asset / timeframe / regime."""
    out: Dict[str, Dict[str, SliceStats]] = {}
    for st in SETUP_TYPES:
        st_trades = [t for t in all_trades if t.setup_type == st]
        cells: Dict[str, SliceStats] = {}
        if grid == "asset":
            keys = ["BTC", "ETH"]
        elif grid == "timeframe":
            keys = ["4H", "1D"]
        else:
            keys = list(set(t.regime for t in all_trades))
        for k in keys:
            if grid == "asset":
                group = [t for t in st_trades if _is_asset(t, k)]
            elif grid == "timeframe":
                group = [t for t in st_trades if t.timeframe == k]
            else:
                group = [t for t in st_trades if t.regime == k]
            cells[k] = slice_stats(group, len(group), f"{st} {k}")
        out[st] = cells
    return out


def _is_asset(t: BacktestTrade, name: str) -> bool:
    return (t.symbol == "BTC/USDT") if name == "BTC" else (t.symbol == "ETH/USDT")


def trade_mechanics(all_trades: List[BacktestTrade]) -> Dict[str, object]:
    """Exit-reason distribution, immediate-after-entry adverse movement, cost mix."""
    total = len(all_trades)
    by_exit: Dict[str, int] = {}
    for t in all_trades:
        by_exit[t.exit_reason] = by_exit.get(t.exit_reason, 0) + 1

    # Immediate adverse movement: MAE as a fraction of entry (magnitude pre-stop).
    mae = [abs(t.max_adverse_excursion) for t in all_trades if t.entry_price]
    mfe = [abs(t.max_favorable_excursion) for t in all_trades]

    costs = sum(t.fees + t.slippage for t in all_trades)
    gross = sum(t.gross_return for t in all_trades)
    net = sum(t.net_return for t in all_trades)

    am = _avg(mae)
    af = _avg(mfe)
    return {
        "n": total,
        "by_exit": by_exit,
        "pct_by_exit": {k: (v / total if total else 0.0) for k, v in by_exit.items()},
        "mae_avg": am,
        "mfe_avg": af,
        "mae_mfe_ratio": (am / af) if af > 0 else None,
        "costs_total": costs,
        "gross_total": gross,
        "net_total": net,
        "cost_drag": (gross - net) if total else 0.0,
    }


def entry_adverse_ratio(all_trades: List[BacktestTrade]) -> Optional[float]:
    """Avg MAE / avg MFE — how much adverse travel vs favorable per trade."""
    mae = [abs(t.max_adverse_excursion) for t in all_trades]
    mfe = [abs(t.max_favorable_excursion) for t in all_trades]
    am, af = _avg(mae), _avg(mfe)
    if af > 0:
        return am / af
    return None


def chronological_slices(
    dataset: DatasetData, n_periods: int = 4
) -> List[Dict[str, object]]:
    """Split a dataset's trades into chronologically ordered periods."""
    trades = sorted(dataset.trades, key=lambda t: t.entry_ts)
    if not trades:
        return []
    total = len(trades)
    per = max(1, total // n_periods)
    out: List[Dict[str, object]] = []
    for i in range(0, total, per):
        group = trades[i : i + per]
        if not group:
            continue
        st = slice_stats(group, len(group), f"p{i // per}")
        out.append(
            {
                "period": i // per,
                "start_ts": int(group[0].entry_ts),
                "end_ts": int(group[-1].exit_ts),
                "n": len(group),
                "avg_return": st.avg_return,
                "win_rate": st.win_rate,
                "profit_factor": st.profit_factor,
                "expectancy": st.expectancy,
            }
        )
    return out


def _avg(xs) -> float:
    return sum(xs) / len(xs) if xs else 0.0