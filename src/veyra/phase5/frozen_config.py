"""Phase 5 frozen strategy configuration (baseline v1).

The strategy used for all real-data validation is frozen at ``phase5-baseline-v1``
and must NOT be tuned during validation. This module records exactly which
settings and engine/prerelease versions that label represents so that any
validation run is traceable back to an immutable input.

Rules honoured here:
- no parameter/hyperparameter optimisation;
- no tuning thresholds to improve results;
- the label is stamped on every BacktestRun so provenance is explicit.
"""

from __future__ import annotations

from typing import Dict, Optional

from ..config import Settings, get_settings
from ..backtest.engine import BacktestEngine

# The frozen version label stamped on every Phase 5 validation run.
PHASE5_BASELINE_VERSION = "phase5-baseline-v1"

# Engine/prerelease versions that back this baseline (from Phase 3/4).
BASE_ENGINE_VERSIONS: Dict[str, str] = {
    "market_pipeline": "phase2.1",
    "setup_engine": "phase3.1",
    "backtest": "phase4.0",
    "strategy": "veyra-3.3",
}


# Execution assumptions frozen into the baseline (Phase 4 defaults).
FROZEN_EXECUTION = {
    "entry_fee_pct": 0.001,
    "exit_fee_pct": 0.001,
    "slippage_pct": 0.0,
    "spread_pct": 0.0,
    "entry_policy": "next_open",
    "ambiguous_candle_policy": "stop_first",
    "overlap_policy": "ALLOW_OVERLAP",
    "position_max_bars": 60,
}


def snapshot_settings(settings: Settings) -> Dict[str, object]:
    """Record the full configuration the frozen baseline runs under.

    Primary fields are captured individually so the report is readable; the
    complete ``settings`` is included for exact reproducibility.
    """
    s = settings
    return {
        "strategy_version": PHASE5_BASELINE_VERSION,
        "engine_versions": dict(BASE_ENGINE_VERSIONS),
        "execution": dict(FROZEN_EXECUTION),
        "timeframes": list(s.timeframes),
        "primary_timeframe": s.primary_timeframe,
        "indicators": {
            "ema_fast": s.ema_fast,
            "ema_slow": s.ema_slow,
            "ema_slope_lookback": s.ema_slope_lookback,
            "swing_lookback": s.swing_lookback,
            "structure_min_pivots": s.structure_min_pivots,
            "rsi_period": s.rsi_period,
            "macd_fast": s.macd_fast,
            "macd_slow": s.macd_slow,
            "macd_signal": s.macd_signal,
            "atr_period": s.atr_period,
            "volume_ma_period": s.volume_ma_period,
        },
        "regime_thresholds": {
            "volatility_low_ratio": s.volatility_low_ratio,
            "volatility_high_ratio": s.volatility_high_ratio,
            "volatility_extreme_ratio": s.volatility_extreme_ratio,
            "volume_expansion_ratio": s.volume_expansion_ratio,
            "volume_contraction_ratio": s.volume_contraction_ratio,
        },
        "scoring_weights": {
            "trend": s.weight_trend,
            "structure": s.weight_structure,
            "pullback": s.weight_pullback,
            "momentum": s.weight_momentum,
            "volume": s.weight_volume,
            "volatility": s.weight_volatility,
        },
        "setup_thresholds": {
            "min_trend_strength": s.setup_min_trend_strength,
            "min_structure_score": s.setup_min_structure_score,
            "min_qualify_score": s.setup_min_qualify_score,
            "max_lifetime_bars": s.setup_max_lifetime_bars,
            "pullback_min_retrace": s.setup_pullback_min_retrace,
            "pullback_max_retrace": s.setup_pullback_max_retrace,
            "breakout_distance_pct": s.setup_breakout_distance_pct,
            "retest_tolerance_pct": s.setup_retest_tolerance_pct,
            "retest_max_bars": s.setup_retest_max_bars,
            "range_boundary_tolerance_pct": s.setup_range_boundary_tolerance_pct,
        },
        "settings_dump": s.model_dump(),
    }


def build_baseline_engine(
    settings: Optional[Settings] = None,
    strategy_version: str = PHASE5_BASELINE_VERSION,
    progressive: bool = False,
) -> BacktestEngine:
    """Construct a BacktestEngine stamped with the frozen Phase 5 baseline.

    ``progressive`` selects the O(N) single-pass analysis mode used for real-data
    validation (the slice path is O(N^2) and intractable on full 4H datasets).
    """
    return BacktestEngine(
        settings or get_settings(),
        strategy_version=strategy_version,
        progressive=progressive,
    )


def snapshot_json() -> str:
    import json

    return json.dumps(snapshot_settings(get_settings()), indent=2, sort_keys=True)