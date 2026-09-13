"""Unit tests for the RegimeEngine decision rules.

These build EngineResults directly to isolate the decision logic.
"""

from __future__ import annotations

from veyra.domain import (
    IndicatorState,
    Regime,
    StructureState,
    TrendDirection,
    VolatilityState,
)
from veyra.market.engine import EngineResult
from veyra.market.regime import RegimeEngine


def _ready(state: dict) -> EngineResult:
    meta = {"state": IndicatorState.READY.value}
    meta.update(state)
    return EngineResult(value=0.0, score=0, detail="", meta=meta)


def _trend(direction: str) -> EngineResult:
    return _ready({"direction": direction, "strength": 80.0})


def _structure(state: str) -> EngineResult:
    return _ready({"structure": state})


def _vol(vol_state: str) -> EngineResult:
    return _ready({"volatility_state": vol_state})


def test_bullish_regime():
    eng = RegimeEngine()
    trend = _trend(TrendDirection.BULLISH.value)
    structure = _structure(StructureState.HIGHER_HIGHS_HIGHER_LOWS.value)
    volatility = _vol(VolatilityState.NORMAL.value)
    res = eng.resolve(trend, structure, volatility)
    assert res.value == Regime.BULL.value
    assert res.meta["regime"] == Regime.BULL.value
    assert res.meta["evidence"]["trend"] == TrendDirection.BULLISH.value


def test_bearish_regime():
    eng = RegimeEngine()
    trend = _trend(TrendDirection.BEARISH.value)
    structure = _structure(StructureState.LOWER_HIGHS_LOWER_LOWS.value)
    volatility = _vol(VolatilityState.NORMAL.value)
    res = eng.resolve(trend, structure, volatility)
    assert res.value == Regime.BEAR.value


def test_high_volatility_dominates():
    eng = RegimeEngine()
    # Even a bullish trend => HIGH_VOLATILITY regime wins.
    trend = _trend(TrendDirection.BULLISH.value)
    structure = _structure(StructureState.HIGHER_HIGHS_HIGHER_LOWS.value)
    volatility = _vol(VolatilityState.EXTREME.value)
    res = eng.resolve(trend, structure, volatility)
    assert res.value == Regime.HIGH_VOLATILITY.value


def test_ranging_regime_when_neutral_trend():
    eng = RegimeEngine()
    trend = _trend(TrendDirection.NEUTRAL.value)
    structure = _structure(StructureState.NEUTRAL.value)
    volatility = _vol(VolatilityState.NORMAL.value)
    res = eng.resolve(trend, structure, volatility)
    assert res.value == Regime.RANGE.value


def test_unknown_when_insufficient_input():
    eng = RegimeEngine()
    trend = _trend(TrendDirection.BULLISH.value)
    structure = EngineResult(
        meta={"state": IndicatorState.INSUFFICIENT_DATA.value}
    )
    volatility = _vol(VolatilityState.NORMAL.value)
    res = eng.resolve(trend, structure, volatility)
    assert res.value == Regime.UNKNOWN.value
    assert res.score == 0


def test_evidence_retained_for_explainability():
    eng = RegimeEngine()
    res = eng.resolve(
        _trend(TrendDirection.BULLISH.value),
        _structure(StructureState.HIGHER_HIGHS_HIGHER_LOWS.value),
        _vol(VolatilityState.NORMAL.value),
    )
    ev = res.meta["evidence"]
    assert "trend" in ev and "structure" in ev and "volatility" in ev