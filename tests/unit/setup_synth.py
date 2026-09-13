"""Synthetic MarketSnapshot builders for setup-detector tests.

Detectors consume a Phase 2 MarketSnapshot, so unit tests construct snapshots
with controlled component meta directly rather than running the full pipeline.
These builders keep per-component meta explicit and deterministic.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from veyra.domain import (
    DataQualityState,
    MarketSide,
    Regime,
    SystemState,
)
from veyra.domain.data_quality import DataQuality
from veyra.domain.snapshot import (
    AnalysisComponentOutput,
    MarketSnapshot,
    RegimeOutput,
)


def _output(name: str, state: str, **meta: Any) -> AnalysisComponentOutput:
    score = int(meta.pop("score", 0))
    return AnalysisComponentOutput(
        name=name,
        state=state,
        score=max(0, min(100, score)),
        meta=meta,
        evidence=meta,
    )


def snapshot(
    regime: Regime = Regime.BULL,
    trend_meta: Optional[Dict[str, Any]] = None,
    structure_meta: Optional[Dict[str, Any]] = None,
    momentum_meta: Optional[Dict[str, Any]] = None,
    volume_meta: Optional[Dict[str, Any]] = None,
    volatility_meta: Optional[Dict[str, Any]] = None,
    timestamp: int = 1_000_000_000,
    timeframe: str = "4H",
    data_quality_state: DataQualityState = DataQualityState.VALID,
    overall_score: int = 0,
) -> MarketSnapshot:
    # Make DataQuality.row_count/required_lookback consistent with the state
    # so DataQuality.resolve() reports the requested state.
    row_count = 300
    required = 210
    validation_valid = True
    if data_quality_state == DataQualityState.INSUFFICIENT_DATA:
        row_count, required = 100, 210
    if data_quality_state == DataQualityState.INVALID:
        validation_valid = False
    snap = MarketSnapshot(
        symbol="BTC/USDT",
        timeframe=timeframe,
        timestamp=timestamp,
        regime=regime,
        overall_score=overall_score,
        system_state=SystemState.WAIT,
        regime_output=RegimeOutput(regime=regime),
        data_quality=DataQuality(
            state=data_quality_state,
            row_count=row_count,
            required_lookback=required,
            validation_valid=validation_valid,
        ),
    )
    snap.components["TREND"] = _output(
        "TREND", "READY", score=trend_score(regime), **(trend_meta or {})
    )
    snap.components["STRUCTURE"] = _output(
        "STRUCTURE", "READY", score=structure_score(), **(structure_meta or {})
    )
    snap.components["MOMENTUM"] = _output(
        "MOMENTUM", "READY", score=70, **(momentum_meta or {})
    )
    snap.components["VOLUME"] = _output("VOLUME", "READY", score=60, **(volume_meta or {}))
    snap.components["VOLATILITY"] = _output(
        "VOLATILITY", "READY", score=70, **(volatility_meta or {})
    )
    return snap


def trend_score(regime: Regime) -> int:
    if regime == Regime.BULL:
        return 90
    if regime == Regime.BEAR:
        return 90
    return 50


def structure_score() -> int:
    return 85


def bull_meta(**overrides: Any) -> Dict[str, Any]:
    d = {"direction": "BULLISH", "strength": 80.0}
    d.update(overrides)
    return d


def bear_meta(**overrides: Any) -> Dict[str, Any]:
    d = {"direction": "BEARISH", "strength": 80.0}
    d.update(overrides)
    return d


def hh_hl_meta(**overrides: Any) -> Dict[str, Any]:
    d = {
        "structure": "HH_HL",
        "action": "NONE",
        "value": 110.0,
        "last_swing_high": 108.0,
        "last_swing_low": 104.0,
    }
    d.update(overrides)
    return d


def lh_ll_meta(**overrides: Any) -> Dict[str, Any]:
    d = {
        "structure": "LH_LL",
        "action": "NONE",
        "value": 90.0,
        "last_swing_high": 94.0,
        "last_swing_low": 92.0,
    }
    d.update(overrides)
    return d