"""Regime engine.

Classifies the current market environment from the analysis engines'
outputs using explicit deterministic rules:

    Regime = f(Trend, Structure, Volatility)

Rules (documented):
- HIGH_VOLATILITY wins when volatility is HIGH or EXTREME (regardless of
  trend), because high volatility can invalidate otherwise-clean trends.
- Otherwise BULL when trend is BULLISH AND structure is bullish
  (HH_HL) AND volatility is not EXTREME.
- Otherwise BEAR when trend is BEARISH AND structure is bearish
  (LH_LL) AND volatility is not EXTREME.
- Otherwise RANGE when trend is NEUTRAL (or mixed structure) and
  volatility is LOW/NORMAL.
- UNKNOWN when any required input is UNKNOWN / INSUFFICIENT_DATA.

Every classification retains the underlying evidence that caused it so the
Analyst layer can explain without an LLM.
"""

from __future__ import annotations

from ..domain import (
    IndicatorState,
    Regime,
    StructureState,
    TrendDirection,
    VolatilityState,
)
from .engine import AnalysisEngine, EngineResult


class RegimeEngine(AnalysisEngine):
    name = "regime"

    def required_columns(self) -> list[str]:
        return ["open", "high", "low", "close", "volume"]

    def resolve(self, trend: EngineResult, structure: EngineResult, volatility: EngineResult) -> EngineResult:
        """Resolve regime from three analysis results (deterministic)."""
        t_state = IndicatorState(trend.meta.get("state", IndicatorState.UNKNOWN.value))
        s_state = IndicatorState(structure.meta.get("state", IndicatorState.UNKNOWN.value))
        v_state = IndicatorState(volatility.meta.get("state", IndicatorState.UNKNOWN.value))

        # Insufficient data in any input => cannot classify reliably.
        if (
            t_state != IndicatorState.READY
            or s_state != IndicatorState.READY
            or v_state != IndicatorState.READY
        ):
            return EngineResult(
                value=Regime.UNKNOWN.value,
                score=0,
                detail="Regime UNKNOWN: one or more inputs insufficient.",
                meta={
                    "regime": Regime.UNKNOWN.value,
                    "evidence": {
                        "trend_state": t_state.value,
                        "structure_state": s_state.value,
                        "volatility_state": v_state.value,
                    },
                },
            )

        trend_dir = TrendDirection(trend.meta["direction"])
        struct = StructureState(structure.meta["structure"])
        vol = VolatilityState(volatility.meta["volatility_state"])

        evidence = {
            "trend": trend_dir.value,
            "structure": struct.value,
            "volatility": vol.value,
            "trend_strength": trend.meta.get("strength"),
            "structure_score": structure.score,
        }

        regime = self._decide(trend_dir, struct, vol)
        detail = f"Regime {regime.value}."

        return EngineResult(
            value=regime.value,
            score=self._regime_score(regime, trend_dir, struct, vol),
            detail=detail,
            meta={"regime": regime.value, "evidence": evidence},
        )

    # -- AnalysisEngine interface (kept for contract compliance) ----------

    def analyze(self, df) -> EngineResult:
        return EngineResult(
            value=Regime.UNKNOWN.value,
            score=0,
            detail="Regime must be resolved from engine outputs via resolve().",
            meta={"regime": Regime.UNKNOWN.value, "evidence": {}},
        )

    # -- Decision rules ----------------------------------------------------

    @staticmethod
    def _decide(
        trend: TrendDirection, struct: StructureState, vol: VolatilityState
    ) -> Regime:
        if vol in (VolatilityState.HIGH, VolatilityState.EXTREME):
            return Regime.HIGH_VOLATILITY

        if trend == TrendDirection.BULLISH and struct in (
            StructureState.HIGHER_HIGHS_HIGHER_LOWS,
            StructureState.HIGHER_HIGHS_LOWER_LOWS,
        ):
            return Regime.BULL

        if trend == TrendDirection.BEARISH and struct in (
            StructureState.LOWER_HIGHS_LOWER_LOWS,
            StructureState.LOWER_HIGHS_HIGHER_LOWS,
        ):
            return Regime.BEAR

        # Range: neutral trend or mixed structure with controlled volatility.
        return Regime.RANGE

    @staticmethod
    def _regime_score(
        regime: Regime,
        trend: TrendDirection,
        struct: StructureState,
        vol: VolatilityState,
    ) -> int:
        """0-100 alignment score reflecting regime decisiveness (not a prob)."""
        if regime == Regime.UNKNOWN:
            return 0
        if regime == Regime.HIGH_VOLATILITY:
            return 55
        if regime == Regime.RANGE:
            return 45
        # BULL/BEAR confidence from structure + volatility alignment.
        score = 45.0
        if struct in (
            StructureState.HIGHER_HIGHS_HIGHER_LOWS,
            StructureState.LOWER_HIGHS_LOWER_LOWS,
        ):
            score += 35.0
        elif struct in (
            StructureState.HIGHER_HIGHS_LOWER_LOWS,
            StructureState.LOWER_HIGHS_HIGHER_LOWS,
        ):
            score += 15.0
        if vol == VolatilityState.NORMAL:
            score += 10.0
        if trend != TrendDirection.NEUTRAL:
            score += 10.0
        return int(round(min(100.0, score)))