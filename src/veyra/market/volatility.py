"""Volatility engine.

Deterministic volatility analysis using ATR:
- ATR (true range average)
- ATR as a percentage of price
- volatility state (LOW / NORMAL / HIGH / EXTREME)
- range expansion/contraction vs prior ATR

Thresholds are configurable and documented as initial hypotheses to be
validated in later phases, not proven optimal values.
"""

from __future__ import annotations

from typing import List

import pandas as pd

from ..domain import IndicatorState, VolatilityState
from .engine import AnalysisEngine, EngineResult


class VolatilityEngine(AnalysisEngine):
    name = "volatility"

    def __init__(
        self,
        atr_period: int = 14,
        low_pct: float = 0.6,
        high_pct: float = 1.4,
        extreme_pct: float = 2.0,
    ) -> None:
        self._p = atr_period
        self._low = low_pct
        self._high = high_pct
        self._extreme = extreme_pct

    def required_columns(self) -> list[str]:
        return ["open", "high", "low", "close"]

    def warmup_required(self) -> int:
        return self._p + 5

    def analyze(self, df: pd.DataFrame) -> EngineResult:
        n = len(df)
        if n < self.warmup_required():
            return EngineResult(
                value=None,
                score=0,
                detail=f"Insufficient data: need >={self.warmup_required()} candles, got {n}",
                meta={
                    "state": IndicatorState.INSUFFICIENT_DATA.value,
                    "volatility_state": VolatilityState.UNKNOWN.value,
                    "required": self.warmup_required(),
                    "actual": n,
                },
            )

        atr = self._atr(df)
        atr_pct = self._atr_percent(atr, float(df["close"].iloc[-1]))

        # Compare current ATR to a trailing average of ATR to classify regime.
        atr_series = self._atr_series(df)
        cur = atr_series.iloc[-1]
        anchor = atr_series.iloc[-1 - self._p]  # ATR p bars ago (expansion basis)
        expansion_ratio = cur / anchor if anchor > 0 else 1.0

        vol_state = self._classify_state(expansion_ratio)

        evidence = {
            "atr": round(float(cur), 6),
            "atr_percent": round(atr_pct, 6),
            "atr_p_bars_ago": round(float(anchor), 6),
            "expansion_ratio": round(expansion_ratio, 4),
        }

        score = self._volatility_score(expansion_ratio, atr_pct)

        return EngineResult(
            value=cur,
            score=score,
            detail=self._describe(vol_state, atr_pct, expansion_ratio),
            meta={
                "state": IndicatorState.READY.value,
                "volatility_state": vol_state.value,
                "atr": round(float(cur), 6),
                "atr_percent": round(atr_pct, 6),
                "expansion_ratio": round(expansion_ratio, 4),
                "evidence": evidence,
            },
        )

    def _atr_series(self, df: pd.DataFrame) -> "pd.Series":
        high = df["high"]
        low = df["low"]
        close = df["close"].shift(1)
        tr = pd.concat(
            [
                high - low,
                (high - close).abs(),
                (low - close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        return tr.ewm(alpha=1.0 / self._p, adjust=False).mean()

    def _atr(self, df: pd.DataFrame) -> float:
        return float(self._atr_series(df).iloc[-1])

    @staticmethod
    def _atr_percent(atr: float, price: float) -> float:
        if price <= 0:
            return 0.0
        return atr / price * 100.0

    def _classify_state(self, expansion_ratio: float) -> VolatilityState:
        if expansion_ratio >= self._extreme:
            return VolatilityState.EXTREME
        if expansion_ratio >= self._high:
            return VolatilityState.HIGH
        if expansion_ratio <= self._low:
            return VolatilityState.LOW
        return VolatilityState.NORMAL

    @staticmethod
    def _volatility_score(expansion_ratio: float, atr_pct: float) -> int:
        """0-100. Not a probability; a normalized measure of volatility level."""
        # Map ATR% into 0-100; higher ATR% = higher score.
        score = min(100.0, atr_pct / 3.0 * 100.0)
        # Add a small component for expansion above baseline.
        if expansion_ratio > 1.0:
            score = min(100.0, score + (expansion_ratio - 1.0) * 20.0)
        return int(round(score))

    @staticmethod
    def _describe(
        vol_state: VolatilityState, atr_pct: float, expansion_ratio: float
    ) -> str:
        return (
            f"Volatility {vol_state.value} (ATR% {atr_pct:.2f}, "
            f"expansion {expansion_ratio:.2f})."
        )

    # -- Progressive (per-index) path --------------------------------------

    def analyze_each(self, df: pd.DataFrame) -> "List[EngineResult]":
        """One causal pass; index i is bit-identical to ``analyze(df[:i+1])``.

        The ATR series is a causal EWM over true-range, so the full-frame value
        at index i equals the prefix-computed value at i.
        """
        n = len(df)
        if n == 0:
            return []
        wu = self.warmup_required()
        atr_series = self._atr_series(df)
        close = df["close"]
        out: list = []
        insuff = {
            "state": IndicatorState.INSUFFICIENT_DATA.value,
            "volatility_state": VolatilityState.UNKNOWN.value,
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
            out.append(self._build_vol_result(atr_series, close, i))
        return out

    def _build_vol_result(self, atr_series, close, i: int) -> EngineResult:
        cur = float(atr_series.iloc[i])
        price = float(close.iloc[i])
        atr_pct = self._atr_percent(cur, price)
        anchor = float(atr_series.iloc[i - self._p])
        expansion_ratio = cur / anchor if anchor > 0 else 1.0
        vol_state = self._classify_state(expansion_ratio)
        evidence = {
            "atr": round(cur, 6),
            "atr_percent": round(atr_pct, 6),
            "atr_p_bars_ago": round(anchor, 6),
            "expansion_ratio": round(expansion_ratio, 4),
        }
        score = self._volatility_score(expansion_ratio, atr_pct)
        return EngineResult(
            value=cur,
            score=score,
            detail=self._describe(vol_state, atr_pct, expansion_ratio),
            meta={
                "state": IndicatorState.READY.value,
                "volatility_state": vol_state.value,
                "atr": round(cur, 6),
                "atr_percent": round(atr_pct, 6),
                "expansion_ratio": round(expansion_ratio, 4),
                "evidence": evidence,
            },
        )