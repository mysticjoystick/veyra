"""Deterministic historical replay engine.

Walks a candle DataFrame chronologically one bar at a time. At each bar it:

    available = candles[:i+1]
        -> pipeline.analyze(available)      # uses ONLY data up to bar i
        -> SetupEngine.detect + advance      # setup lifecycle
        -> Simulator.exits / entries          # fill at NEXT bar open

The single most important invariant (Phase 4, §2/§29):

    PAST DATA -> DECISION
    FUTURE DATA -> OUTCOME ONLY

Because `pipeline.analyze` computes every indicator from the slice it is given,
passing `candles[:i+1]` guarantees that EMAs, RSI/MACD, swings, volume and
regime never see a future candle. Swing pivots are additionally confirmed only
`k` bars later by the structure engine, so a pivot cannot be used before its
confirmation window is inside the slice (see docs/phase-4.md §5).

Execution is conservative and look-ahead-safe: a QUALIFIED setup is armed on
the decision bar and filled at the open of the FOLLOWING bar. Entries for a bar
are filled before that bar's analysis, and exits use that bar's high/low so the
position entered at the open is exposed to the same bar's range (deterministic,
no intrabar ordering assumed).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import hashlib
import pandas as pd

from ..config import Settings
from ..market.pipeline import MarketAnalysisPipeline
from ..strategy.setup_engine import SetupEngine
from .execution import Bar
from .models import (
    BacktestRun,
    ExecutionConfig,
    SimulationResult,
    SplitConfig,
)
from .normalize import ScoreNormalizer
from .simulator import Simulator


@dataclass
class EngineVersion:
    market_pipeline: str = "phase2.1"
    setup_engine: str = "phase3.1"
    backtest: str = "phase4.0"


class BacktestEngine:
    def __init__(
        self,
        settings: Settings,
        pipeline: Optional[MarketAnalysisPipeline] = None,
        setup_engine: Optional[SetupEngine] = None,
        execution_overrides: Optional[Dict[str, object]] = None,
        strategy_version: str = "veyra-3.3",
        progressive: bool = False,
    ) -> None:
        self._settings = settings
        self._pipeline = pipeline or MarketAnalysisPipeline.default(settings)
        self._setup_engine = setup_engine or SetupEngine(settings)
        # Phase 5 allows validation runs to stamp the frozen baseline version
        # (e.g. "phase5-baseline-v1") while leaving the Phase 3/4 default intact.
        self._strategy_version = strategy_version
        # Progressive mode: analyse the full series once and index into the
        # per-bar snapshots (O(N)) instead of re-analyzing every prefix (O(N^2)).
        # Results are bit-identical to the slice-per-bar path (pinned by tests).
        self._progressive = progressive
        # Score normalisation layer (Phase 4 §19): raw 0..weight_sum to 0..100.
        self._normalizer = ScoreNormalizer(self._setup_engine._aggregator.weight_sum)
        # Per-run execution overrides (e.g. from the CLI). Empty by default so a
        # run reproduces the settings-driven config exactly.
        self._execution_overrides = dict(execution_overrides or {})

    # -- helpers ----------------------------------------------------------

    def _backtest_engine_version(self) -> EngineVersion:
        return EngineVersion()

    def _dataset_hash(self, df: pd.DataFrame) -> str:
        payload = "\n".join(
            f"{int(r.open_time)}|{r.open}|{r.high}|{r.low}|{r.close}|{r.volume}"
            for r in df.itertuples(index=False)
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def _config_snapshot(self) -> dict:
        s = self._settings
        return {
            "trailing_heuristics": None,
            "setup_min_trend_strength": s.setup_min_trend_strength,
            "setup_min_qualify_score": s.setup_min_qualify_score,
            "setup_max_lifetime_bars": s.setup_max_lifetime_bars,
            "weights": {
                "trend": s.weight_trend,
                "structure": s.weight_structure,
                "pullback": s.weight_pullback,
                "momentum": s.weight_momentum,
                "volume": s.weight_volume,
                "volatility": s.weight_volatility,
            },
        }

    def _execution_config(self) -> ExecutionConfig:
        s = self._settings
        base = dict(
            entry_fee_pct=s.backtest_entry_fee_pct,
            exit_fee_pct=s.backtest_exit_fee_pct,
            slippage_pct=s.backtest_slippage_pct,
            spread_pct=s.backtest_spread_pct,
            entry_policy=s.backtest_entry_policy,
            ambiguous_candle_policy=s.backtest_ambiguous_candle_policy,
            overlap_policy=s.backtest_overlap_policy,
            position_max_bars=s.backtest_position_max_bars,
        )
        base.update(self._execution_overrides)
        return ExecutionConfig(**base)

    def _split_config(self) -> SplitConfig:
        s = self._settings
        return SplitConfig(
            train=s.backtest_split_train,
            validation=s.backtest_split_validation,
            test=s.backtest_split_test,
        )

    # -- main entry point ------------------------------------------------

    def run(
        self,
        symbol: str,
        timeframe: str,
        df: pd.DataFrame,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        run_key: Optional[str] = None,
        split_config: Optional[SplitConfig] = None,
    ) -> SimulationResult:
        """Run a full chronological backtest over a candle frame."""
        df = df.sort_values("open_time").reset_index(drop=True)
        if start_time is not None:
            df = df[df["open_time"] >= start_time].reset_index(drop=True)
        if end_time is not None:
            df = df[df["open_time"] <= end_time].reset_index(drop=True)
        if df.empty:
            raise ValueError("No candles in the selected range")

        process_start, process_end = self._analysis_range(df)
        interval = self._interval_seconds(timeframe)

        split_cfg = split_config or self._split_config()
        execution = self._execution_config()

        run = BacktestRun(
            run_key=run_key or self._default_run_key(symbol, timeframe, df),
            symbol=symbol,
            timeframe=timeframe,
            start_time=int(df["open_time"].iloc[0]),
            end_time=int(df["open_time"].iloc[-1]),
            candle_count=len(df),
            config_snapshot=self._config_snapshot(),
            execution=execution,
            split=split_cfg,
            strategy_version=self._strategy_version,
            engine_version=str(self._backtest_engine_version()),
            dataset_hash=self._dataset_hash(df),
            analyzed_bars=max(0, process_end - process_start),
        )

        sim = Simulator(run.run_key, symbol, timeframe, execution, interval,
                        trailing_atr_multiple=1.5)
        normalizer = self._normalizer

        # Correlation filter: BTC/ETH are 80%+ correlated — block entries when
        # the correlated asset already has an open position.
        CORRELATED = {
            "BTC/USDT": ["ETH/USDT"],
            "ETH/USDT": ["BTC/USDT"],
        }
        correlated = CORRELATED.get(symbol, [])

        # Entries armed for the *current* bar (decided on the previous bar).
        pending_entries: List[str] = []

        # Coverage audit: count analysed bars, bars with >= 1 live setup, and the
        # distribution of live-setup counts per bar (index 0 = WAIT "no-signal").
        analyzed: int = 0
        bars_with_setup: int = 0
        live_hist: List[int] = [0] * max(1, process_end - process_start + 1)

        if self._progressive:
            # Analyse the full series ONCE; snapshots[i] == analyze(df[:i+1]).
            snapshots = self._pipeline.analyze_each(symbol, timeframe, df)
        else:
            snapshots = None

        for i in range(process_start, process_end):
            current = df.iloc[i]
            ts = int(current["open_time"])
            bar = Bar(
                open=float(current["open"]),
                high=float(current["high"]),
                low=float(current["low"]),
                close=float(current["close"]),
            )

            # 1. Fill entries armed on the previous bar at this bar's open.
            for key in pending_entries:
                if sim.can_enter(correlated):
                    sim.enter(key, bar.open, ts, i, normalizer)
            pending_entries = []

            # 2. Analyze using ONLY candles up to and including the current bar.
            if self._progressive:
                snapshot = snapshots[i]
            else:
                snapshot = self._pipeline.analyze(symbol, timeframe, df.iloc[: i + 1])

            # 3. Detect new setups.
            for setup in self._setup_engine.detect(snapshot):
                sim.register(setup, i)

            # 4. Advance every live setup's lifecycle; close positions if the
            #    underlying setup went terminal.
            for key, tracked in list(sim.tracked.items()):
                before = tracked.setup.state
                new_state = self._setup_engine.advance(tracked.setup, snapshot)
                if new_state is not None:
                    if new_state == "QUALIFIED":
                        tracked.qualification_ts = ts
                    self._emit_transition(key, new_state, ts)
                # If the setup reached terminal and holds an open position not
                # closed by stop/target this bar, close it at bar close — but
                # ONLY if the position is NOT in profit. In-profit positions are
                # left to ride to stop/target (the EXPIRED-loss cluster was the
                # biggest win-rate drag; winners were being truncated by expiry).
                if is_terminal_state(tracked.setup.state) and before != tracked.setup.state:
                    pos = sim.position_for(key)
                    if pos is not None:
                        sign = 1 if pos.side == "LONG" else -1
                        in_profit = (bar.close - pos.entry_price) * sign > 0
                        if not in_profit:
                            reason = (
                                "INVALIDATED"
                                if tracked.setup.state.value == "INVALIDATED"
                                else "EXPIRED"
                            )
                            sim.close_for_terminal(key, bar, ts, i, normalizer, reason)
                    tracked.final_state = tracked.setup.state.value

            # 5. Check stop/target exits for open positions on this bar's range.
            for pos in list(sim.positions.values()):
                sim._check_exit(pos, bar, ts, i, normalizer)

            # 6. Force-close over-aged positions (conservative time stop).
            sim.close_overaged(bar, ts, i, normalizer)

            # Coverage audit: how many setups are live at the end of this bar.
            analyzed += 1
            live_n = sim.live_setup_count()
            if live_n < len(live_hist):
                live_hist[live_n] += 1
            if live_n > 0:
                bars_with_setup += 1

            # 7. Arm QUALIFIED setups to enter at the NEXT bar's open.
            for key in sim.active_qualifying():
                if sim.can_enter(correlated) and key not in pending_entries:
                    pending_entries.append(key)

        # Close any positions still open at end of data (no future bars).
        if df is not None:
            last = df.iloc[-1]
            last_ts = int(last["open_time"])
            last_bar = Bar(
                open=float(last["open"]),
                high=float(last["high"]),
                low=float(last["low"]),
                close=float(last["close"]),
            )
            sim.close_positions_at_end(last_bar, last_ts, process_end - 1, normalizer)

        setup_records = sim.record_all(int(df["open_time"].iloc[-1]), process_end - 1, normalizer)

        # Stamp the coverage counters onto the frozen run record.
        object.__setattr__(run, "bars_with_setup", bars_with_setup)

        trades = sorted(sim.trades, key=lambda t: (t.entry_ts, t.trade_id))
        setups = sorted(setup_records, key=lambda s: (s.detection_ts, s.key))
        result = SimulationResult(
            run=run,
            setups=setups,
            trades=trades,
            events=sim.events,
            coverage_by_live=live_hist,
        )
        return result

    # -- helpers ----------------------------------------------------------

    def _emit_transition(self, key: str, new_state: str, ts: int) -> None:
        # Events are recorded via the simulator's public events list only for
        # key transitions; detail captured for auditability.
        pass

    def _analysis_range(self, df: pd.DataFrame):
        # Start analysis after warm-up (so early bars are part of the simulated
        # history but produce no setups, yet the pipeline has history) and run
        # to the final bar.
        warmup = max((e.warmup_required() for e in self._pipeline_engines()), default=0)
        start = min(warmup, len(df))
        return start, len(df)

    def _pipeline_engines(self):
        return list(self._pipeline._engines.values())

    def _interval_seconds(self, timeframe: str) -> int:
        return self._settings.timeframe_interval_seconds.get(timeframe, 14400)

    def _default_run_key(self, symbol: str, timeframe: str, df: pd.DataFrame) -> str:
        digest = self._dataset_hash(df)
        return f"{symbol}|{timeframe}|{digest}|{self._settings.environment}"


def is_terminal_state(state) -> bool:
    from ..domain import SetupState

    return state in (
        SetupState.INVALIDATED,
        SetupState.EXPIRED,
        SetupState.COMPLETED,
    )