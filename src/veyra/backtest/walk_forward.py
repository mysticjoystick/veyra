"""Walk-forward validation driver.

Sweeps a chronological window across the dataset, running a full BacktestEngine
per window, and aggregates the per-window results. Used to test whether the
current hypotheses remain stable across different historical periods — NOT to
optimise parameters (Phase 4 is measurement and falsification only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd

from ..config import Settings
from .engine import BacktestEngine
from .metrics import MetricsEngine, MetricsResult
from .split import walk_forward_windows


@dataclass
class WalkForwardResult:
    windows: List[MetricsResult] = field(default_factory=list)
    window_meta: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "window_count": len(self.windows),
            "window_meta": list(self.window_meta),
            "windows": [w.to_dict() for w in self.windows],
        }


class WalkForwardRunner:
    def __init__(
        self,
        settings: Settings,
        engine: Optional[BacktestEngine] = None,
        metrics: Optional[MetricsEngine] = None,
    ) -> None:
        self._settings = settings
        self._engine = engine or BacktestEngine(settings)
        self._metrics = metrics or MetricsEngine()

    def run(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        train_bars: Optional[int] = None,
        validation_bars: Optional[int] = None,
        test_bars: Optional[int] = None,
        step_bars: Optional[int] = None,
    ) -> WalkForwardResult:
        df = df.sort_values("open_time").reset_index(drop=True)
        train_bars = train_bars or self._settings.walk_forward_train_bars
        validation_bars = validation_bars or self._settings.walk_forward_validation_bars
        test_bars = test_bars or self._settings.walk_forward_test_bars
        step_bars = step_bars or self._settings.walk_forward_step_bars

        result = WalkForwardResult()
        times = df["open_time"].to_numpy()
        for wf in walk_forward_windows(
            len(df), train_bars, validation_bars, test_bars, step_bars
        ):
            # Run the backtest over all data up to the test end so the pipeline
            # has full historical warm-up for the test lane (look-back uses
            # training/validation data; outcomes are only measured in the test
            # period). This preserves out-of-sample integrity per window.
            run = self._engine.run(
                symbol,
                timeframe,
                df.iloc[: wf.test_end],
                run_key=f"{symbol}|{timeframe}|wf{wf.index}",
            )
            test_start_ts = int(times[wf.test_start]) if wf.test_start < len(times) else None
            test_end_ts = int(times[wf.test_end - 1]) if wf.test_end > 0 else None

            window_trades = [
                t for t in run.trades
                if (test_start_ts is None or t.entry_ts >= test_start_ts)
                and (test_end_ts is None or t.entry_ts <= test_end_ts)
            ]
            window_setups = [
                s for s in run.setups
                if (test_start_ts is None or s.detection_ts >= test_start_ts)
                and (test_end_ts is None or s.detection_ts <= test_end_ts)
            ]
            metrics = self._metrics.compute(window_trades, window_setups, len(df))
            result.windows.append(metrics)
            result.window_meta.append(
                {
                    "index": wf.index,
                    "train": (wf.train_start, wf.train_end),
                    "validation": (wf.validation_start, wf.validation_end),
                    "test": (wf.test_start, wf.test_end),
                    "trades": len(window_trades),
                    "completed_setups": len(window_setups),
                }
            )
        return result