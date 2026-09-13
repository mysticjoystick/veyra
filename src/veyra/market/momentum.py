"""Momentum engine.

Deterministic momentum analysis using:
- RSI (14)
- MACD (12/26/9) histogram as the momentum measure
- momentum direction and acceleration/deceleration

Documented warm-up: RSI needs `rsi_period` bars; MACD needs slow period
plus signal period. The engine reports INSUFFICIENT_DATA until all
indicators are meaningful, rather than returning fabricated values.
"""

from __future__ import annotations

from typing import List

import pandas as pd

from ..domain import IndicatorState, MomentumState
from .engine import AnalysisEngine, EngineResult


class MomentumEngine(AnalysisEngine):
    name = "momentum"

    def __init__(
        self,
        rsi_period: int = 14,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        rsi_overbought: float = 70.0,
        rsi_oversold: float = 30.0,
    ) -> None:
        self._rsi = rsi_period
        self._mf = macd_fast
        self._ms = macd_slow
        self._msig = macd_signal
        self._ob = rsi_overbought
        self._os = rsi_oversold

    def required_columns(self) -> list[str]:
        return ["close"]

    def warmup_required(self) -> int:
        # MACD signal line plus margin is the strictest requirement.
        return self._ms + self._msig + 5

    def analyze(self, df: pd.DataFrame) -> EngineResult:
        n = len(df)
        if n < self.warmup_required():
            return EngineResult(
                value=None,
                score=0,
                detail=f"Insufficient data: need >={self.warmup_required()} candles, got {n}",
                meta={
                    "state": IndicatorState.INSUFFICIENT_DATA.value,
                    "momentum": MomentumState.UNKNOWN.value,
                    "required": self.warmup_required(),
                    "actual": n,
                },
            )

        close = df["close"]
        rsi = self._rsi_value(close)
        macd_hist = self._macd_histogram(close)
        prev_hist = self._macd_histogram(close.iloc[:-1])

        momentum = self._decide_momentum(rsi, macd_hist)

        evidence = {
            "rsi": round(float(rsi), 3),
            "macd_histogram": round(float(macd_hist), 6),
            "macd_histogram_prev": round(float(prev_hist), 6),
            "histogram_increasing": bool(macd_hist >= prev_hist),
            "rsi_overbought": bool(rsi >= self._ob),
            "rsi_oversold": bool(rsi <= self._os),
        }

        score = self._momentum_score(momentum, evidence)

        return EngineResult(
            value=rsi,
            score=score,
            detail=self._describe(momentum, rsi, macd_hist),
            meta={
                "state": IndicatorState.READY.value,
                "momentum": momentum.value,
                "rsi": round(float(rsi), 3),
                "macd_histogram": round(float(macd_hist), 6),
                "evidence": evidence,
            },
        )

    def analyze_each(self, df: pd.DataFrame) -> "List[EngineResult]":
        """One causal pass; index i is bit-identical to ``analyze(df[:i+1])``."""
        n = len(df)
        if n == 0:
            return []
        wu = self.warmup_required()
        close = df["close"]
        # Compute RSI series and the MACD line once over the full frame.
        rsi = self._rsi_series(close)
        macd_hist = self._macd_histogram_series(close)
        prev_hist = self._macd_histogram_series(close)

        out: list = []
        insuff = {
            "state": IndicatorState.INSUFFICIENT_DATA.value,
            "momentum": MomentumState.UNKNOWN.value,
            "required": wu,
        }
        for i in range(n):
            if i < wu - 1:
                out.append(
                    EngineResult(
                        value=None,
                        score=0,
                        detail=f"Insufficient data: need >={wu} candles, got {i + 1}",
                        meta=dict(insuff, actual=i + 1),
                    )
                )
                continue
            out.append(self._build_momentum_result(rsi, macd_hist, prev_hist, i))
        return out

    def _build_momentum_result(self, rsi, macd_hist, prev_hist, i: int) -> EngineResult:
        cur_rsi = float(rsi.iloc[i])
        cur_hist = float(macd_hist.iloc[i])
        prev_hist_val = float(prev_hist.iloc[i - 1]) if i >= 1 else None
        if prev_hist_val is None:
            prev_hist_val = cur_hist  # matches analyze(close.iloc[:-1]) on a 1-len series
        momentum = self._decide_momentum(cur_rsi, cur_hist)
        evidence = {
            "rsi": round(cur_rsi, 3),
            "macd_histogram": round(cur_hist, 6),
            "macd_histogram_prev": round(prev_hist_val, 6),
            "histogram_increasing": bool(cur_hist >= prev_hist_val),
            "rsi_overbought": bool(cur_rsi >= self._ob),
            "rsi_oversold": bool(cur_rsi <= self._os),
        }
        score = self._momentum_score(momentum, evidence)
        return EngineResult(
            value=cur_rsi,
            score=score,
            detail=self._describe(momentum, cur_rsi, cur_hist),
            meta={
                "state": IndicatorState.READY.value,
                "momentum": momentum.value,
                "rsi": round(cur_rsi, 3),
                "macd_histogram": round(cur_hist, 6),
                "evidence": evidence,
            },
        )

    def _rsi_series(self, close: "pd.Series") -> "pd.Series":
        delta = close.diff()
        gain = delta.clip(lower=0.0).rolling(self._rsi).mean()
        loss = (-delta.clip(upper=0.0)).rolling(self._rsi).mean()
        rs = gain / loss.replace(0.0, 1e-12)
        return 100.0 - (100.0 / (1.0 + rs))

    def _macd_histogram_series(self, close: "pd.Series") -> "pd.Series":
        ema_fast = close.ewm(span=self._mf, adjust=False).mean()
        ema_slow = close.ewm(span=self._ms, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal = macd_line.ewm(span=self._msig, adjust=False).mean()
        return macd_line - signal

    def _rsi_value(self, close: "pd.Series") -> float:
        return float(self._rsi_series(close).iloc[-1])

    def _macd_histogram(self, close: "pd.Series") -> float:
        return float(self._macd_histogram_series(close).iloc[-1])

    def _decide_momentum(self, rsi: float, macd_hist: float) -> MomentumState:
        # MACD histogram sign is the primary momentum direction; RSI confirms.
        if macd_hist > 0:
            return MomentumState.POSITIVE
        if macd_hist < 0:
            return MomentumState.NEGATIVE
        return MomentumState.NEUTRAL

    def _momentum_score(self, momentum: MomentumState, evidence: dict) -> int:
        """0-100 directional-alignment score (not a probability)."""
        if momentum == MomentumState.UNKNOWN:
            return 0
        directional = momentum in (MomentumState.POSITIVE, MomentumState.NEGATIVE)
        if not directional:
            return 50
        # Base strength 50, add points for magnitude of confirmed histogram.
        mag = abs(evidence["macd_histogram"])
        hist_score = max(0.0, min(50.0, mag * 1000.0))  # scale into 0..50
        score = 50.0 + hist_score
        return int(round(min(100.0, score)))

    @staticmethod
    def _describe(momentum: MomentumState, rsi: float, macd_hist: float) -> str:
        return (
            f"Momentum {momentum.value} (RSI {rsi:.1f}, "
            f"MACD hist {macd_hist:.4f})."
        )