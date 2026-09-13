"""Market analysis pipeline.

Orchestrates the intelligence engines into a single MarketSnapshot:

    validated candle data
        -> trend / structure / momentum / volume / volatility
        -> regime (from trend + structure + volatility)
        -> combat scores + overall score + system state + data quality
        -> MarketSnapshot

The pipeline wires real Phase 2 engines through the AnalysisEngine contract.
It is independent of UI, storage, provider, and setup logic.
"""

from __future__ import annotations

from typing import Dict, List

import pandas as pd

from ..config import Settings
from ..domain import (
    AnalyticsComponent,
    IndicatorState,
    Regime,
    SystemState,
)
from ..domain.data_quality import DataQuality
from ..domain.snapshot import AnalysisComponentOutput, MarketSnapshot, RegimeOutput
from ..market.engine import AnalysisEngine, EngineResult
from ..market.momentum import MomentumEngine
from ..market.regime import RegimeEngine
from ..market.structure import StructureEngine
from ..market.trend import TrendEngine
from ..market.volatility import VolatilityEngine
from ..market.volume import VolumeEngine
from ..strategy.setup_engine import WeightedScoreAggregator

_ENGINE_NAMES = [
    AnalyticsComponent.TREND.value,
    AnalyticsComponent.STRUCTURE.value,
    AnalyticsComponent.MOMENTUM.value,
    AnalyticsComponent.VOLUME.value,
    AnalyticsComponent.VOLATILITY.value,
]


class MarketAnalysisPipeline:
    def __init__(
        self,
        settings: Settings,
        engines: Dict[str, AnalysisEngine],
        regime_engine: RegimeEngine,
        aggregator: WeightedScoreAggregator,
    ) -> None:
        self._settings = settings
        self._engines = engines
        self._regime_engine = regime_engine
        self._aggregator = aggregator

    @classmethod
    def default(cls, settings: Settings) -> "MarketAnalysisPipeline":
        engines: Dict[str, AnalysisEngine] = {
            AnalyticsComponent.TREND.value: TrendEngine(
                ema_fast=settings.ema_fast,
                ema_slow=settings.ema_slow,
                slope_lookback=settings.ema_slope_lookback,
            ),
            AnalyticsComponent.STRUCTURE.value: StructureEngine(
                pivot_lookback=settings.swing_lookback,
                min_pivots=settings.structure_min_pivots,
                use_close_for_break=settings.structure_use_close_for_break,
            ),
            AnalyticsComponent.MOMENTUM.value: MomentumEngine(
                rsi_period=settings.rsi_period,
                macd_fast=settings.macd_fast,
                macd_slow=settings.macd_slow,
                macd_signal=settings.macd_signal,
                rsi_overbought=settings.rsi_overbought,
                rsi_oversold=settings.rsi_oversold,
            ),
            AnalyticsComponent.VOLUME.value: VolumeEngine(
                volume_ma_period=settings.volume_ma_period,
                expansion_ratio=settings.volume_expansion_ratio,
                contraction_ratio=settings.volume_contraction_ratio,
            ),
            AnalyticsComponent.VOLATILITY.value: VolatilityEngine(
                atr_period=settings.atr_period,
                low_pct=settings.volatility_low_ratio,
                high_pct=settings.volatility_high_ratio,
                extreme_pct=settings.volatility_extreme_ratio,
            ),
        }
        regime_engine = RegimeEngine()
        weights = {
            AnalyticsComponent.TREND.value: settings.weight_trend,
            AnalyticsComponent.STRUCTURE.value: settings.weight_structure,
            AnalyticsComponent.PULLBACK.value: settings.weight_pullback,
            AnalyticsComponent.MOMENTUM.value: settings.weight_momentum,
            AnalyticsComponent.VOLUME.value: settings.weight_volume,
            AnalyticsComponent.VOLATILITY.value: settings.weight_volatility,
        }
        return cls(
            settings,
            engines,
            regime_engine,
            WeightedScoreAggregator(weights),
        )

    def analyze(self, symbol: str, timeframe: str, data: pd.DataFrame) -> MarketSnapshot:
        if data.empty:
            raise ValueError(f"No candle data provided for {symbol} {timeframe}")

        timestamp = int(data["open_time"].iloc[-1])
        snapshot = MarketSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=timestamp,
        )
        snapshot.data_quality = self._assess_data_quality(data)

        # 1. Per-component analysis.
        for name in _ENGINE_NAMES:
            engine = self._engines[name]
            result = engine.analyze(data)
            self._record_component(snapshot, name, result)

        # 2. Resolve regime from trend + structure + volatility.
        regime_output = self._resolve_regime(snapshot)
        snapshot.regime_output = regime_output
        snapshot.regime = regime_output.regime

        # 3. Overall score aggregates the component scores.
        score_values = {k.value: v for k, v in snapshot.scores.items()}
        snapshot.overall_score = self._aggregator.aggregate(score_values)
        snapshot.system_state = self._system_state(snapshot.overall_score, snapshot.regime)

        return snapshot

    # -- Progressive (per-index) single pass --------------------------------

    def analyze_each(self, symbol: str, timeframe: str, data: pd.DataFrame) -> "List[MarketSnapshot]":
        """Compute one snapshot per bar in a SINGLE causal pass.

        Each engine's ``analyze_each`` yields index i results identical to
        ``analyze(df[:i+1])`` (pinned by Phase 5 equivalence tests). This lets
        the backtester walk the whole series in O(N) instead of O(N^2) with
        exactly the same, look-ahead-safe semantics.
        """
        if data.empty:
            raise ValueError(f"No candle data provided for {symbol} {timeframe}")

        data = data.sort_values("open_time").reset_index(drop=True)
        results: Dict[str, list] = {}
        for name in _ENGINE_NAMES:
            results[name] = self._engines[name].analyze_each(data)

        snapshots = [
            self._build_snapshot_at(symbol, timeframe, data, i, results)
            for i in range(len(data))
        ]
        return snapshots

    def _build_snapshot_at(self, symbol, timeframe, data, index, results) -> MarketSnapshot:
        sub = data.iloc[: index + 1]
        timestamp = int(sub["open_time"].iloc[-1])
        snapshot = MarketSnapshot(symbol=symbol, timeframe=timeframe, timestamp=timestamp)
        snapshot.data_quality = self._assess_data_quality(sub)
        for name in _ENGINE_NAMES:
            self._record_component(snapshot, name, results[name][index])
        regime_output = self._resolve_regime(snapshot)
        snapshot.regime_output = regime_output
        snapshot.regime = regime_output.regime
        score_values = {k.value: v for k, v in snapshot.scores.items()}
        snapshot.overall_score = self._aggregator.aggregate(score_values)
        snapshot.system_state = self._system_state(snapshot.overall_score, snapshot.regime)
        return snapshot

    # -- Helpers -----------------------------------------------------------

    def _record_component(
        self, snapshot: MarketSnapshot, name: str, result: EngineResult
    ) -> None:
        state = result.meta.get("state", IndicatorState.UNKNOWN.value)
        output = AnalysisComponentOutput(
            name=name,
            state=state,
            score=result.score,
            value=result.value,
            detail=result.detail,
            meta=result.meta,
            evidence=result.meta.get("evidence", {}),
        )
        snapshot.add_component_output(output)
        # Only score READY components; insufficient data scores 0 but we still
        # record it so the snapshot stays truthful about reliability.
        snapshot.set_component(AnalyticsComponent(name), result.score)

    def _resolve_regime(self, snapshot: MarketSnapshot) -> RegimeOutput:
        trend = self._component_meta(snapshot, AnalyticsComponent.TREND.value)
        structure = self._component_meta(snapshot, AnalyticsComponent.STRUCTURE.value)
        volatility = self._component_meta(snapshot, AnalyticsComponent.VOLATILITY.value)

        result = self._regime_engine.resolve(trend, structure, volatility)
        return RegimeOutput(
            regime=Regime(result.value),
            score=result.score,
            evidence=result.meta.get("evidence", {}),
            detail=result.detail,
        )

    @staticmethod
    def _component_meta(snapshot: MarketSnapshot, name: str) -> EngineResult:
        comp = snapshot.components.get(name)
        # Rebuild a light EngineResult so RegimeEngine.resolve has a uniform shape.
        meta = comp.meta if comp else {}
        return EngineResult(
            value=comp.value if comp else None,
            score=comp.score if comp else 0,
            detail=comp.detail if comp else "",
            meta=meta,
        )

    def _assess_data_quality(self, df: pd.DataFrame) -> DataQuality:
        """Infer data quality from validation-looking signals on the frame.

        Phase 1's Validator normally does this; here we do a lightweight pass
        so the analysis pipeline is honest about what it was given. A fuller
        gap/validator report can be attached by callers.
        """
        required_lookback = max(
            (e.warmup_required() for e in self._engines.values()),
            default=0,
        )
        return DataQuality(
            row_count=len(df),
            required_lookback=required_lookback,
        )

    @staticmethod
    def _system_state(overall_score: int, regime: Regime) -> SystemState:
        if overall_score >= 80 and regime != Regime.UNKNOWN:
            return SystemState.ALERT
        if overall_score >= 60:
            return SystemState.MONITORING
        return SystemState.WAIT