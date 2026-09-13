"""Trend engine.

Deterministic trend analysis using EMAs and price position:

- EMA 50 / EMA 200 relationship
- price relative to EMA 50 and EMA 200
- EMA slopes
- trend strength (0-100) combining alignment factors
- structural confirmation of higher highs/lower lows where data allows

The trend direction/strength is a score/classification, NOT a calibrated
probability. No fake confidence percentages.
"""

from __future__ import annotations

from typing import List

import pandas as pd

from ..domain import IndicatorState, TrendDirection
from .engine import AnalysisEngine, EngineResult


class TrendEngine(AnalysisEngine):
    name = "trend"

    def __init__(
        self,
        ema_fast: int = 50,
        ema_slow: int = 200,
        slope_lookback: int = 3,
        discount_penalty: bool = True,
    ) -> None:
        self._fast = ema_fast
        self._slow = ema_slow
        self._slope_lookback = slope_lookback
        self._discount_penalty = discount_penalty

    def required_columns(self) -> list[str]:
        return ["open", "high", "low", "close"]

    def warmup_required(self) -> int:
        # EMA is meaningful only once slow EMA is established, plus margin.
        return self._slow + 10

    def analyze(self, df: pd.DataFrame) -> EngineResult:
        n = len(df)
        if n < self.warmup_required():
            return EngineResult(
                value=None,
                score=0,
                detail=f"Insufficient data: need >={self.warmup_required()} candles, got {n}",
                meta={
                    "state": IndicatorState.INSUFFICIENT_DATA.value,
                    "direction": TrendDirection.UNKNOWN.value,
                    "required": self.warmup_required(),
                    "actual": n,
                },
            )

        close = df["close"]
        ema_fast = close.ewm(span=self._fast, adjust=False).mean()
        ema_slow = close.ewm(span=self._slow, adjust=False).mean()

        last = close.iloc[-1]
        last_ema_fast = ema_fast.iloc[-1]
        last_ema_slow = ema_slow.iloc[-1]
        prev_ema_fast = ema_fast.iloc[-1 - self._slope_lookback]
        prev_ema_slow = ema_slow.iloc[-1 - self._slope_lookback]

        evidence = {
            "price_above_ema_fast": bool(last > last_ema_fast),
            "price_above_ema_slow": bool(last > last_ema_slow),
            "ema_fast_above_ema_slow": bool(last_ema_fast > last_ema_slow),
            "ema_fast_slope_positive": bool(last_ema_fast > prev_ema_fast),
            "ema_slow_slope_positive": bool(last_ema_slow > prev_ema_slow),
        }

        direction = self._decide_direction(evidence, last, last_ema_fast, last_ema_slow)
        strength = self._strength(evidence, direction)

        return EngineResult(
            value=last_ema_fast,
            score=int(round(strength)),
            detail=self._describe(direction, strength),
            meta={
                "state": IndicatorState.READY.value,
                "direction": direction.value,
                "strength": round(strength, 2),
                "evidence": evidence,
                "ema_fast": round(float(last_ema_fast), 6),
                "ema_slow": round(float(last_ema_slow), 6),
                "ema_fast_slope": round(float(last_ema_fast - prev_ema_fast), 6),
                "ema_slow_slope": round(float(last_ema_slow - prev_ema_slow), 6),
            },
        )

    def analyze_each(self, df: pd.DataFrame) -> "List[EngineResult]":
        """One causal pass over the full frame; index i is bit-identical to
        ``analyze(df.iloc[:i+1])`` (verifiable, and pinned by Phase 5 tests).

        This is the building block that lets the backtester avoid re-analyzing
        every prefix from scratch (O(N^2) -> O(N)) while keeping exact,
        look-ahead-safe semantics.
        """
        n = len(df)
        if n == 0:
            return []
        wu = self.warmup_required()
        close = df["close"]
        # Compute each causal series ONCE over the full frame. Because these are
        # causal rolling/recursive quantities, the value at index i of the
        # full-frame series equals the value analyze(df[:i+1]) would produce.
        ema_fast = close.ewm(span=self._fast, adjust=False).mean()
        ema_slow = close.ewm(span=self._slow, adjust=False).mean()

        out: list = []
        insuff = {
            "state": IndicatorState.INSUFFICIENT_DATA.value,
            "direction": TrendDirection.UNKNOWN.value,
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
            out.append(self._build_ema_result(close, ema_fast, ema_slow, i))
        return out

    def _build_ema_result(self, close, ema_fast, ema_slow, i: int) -> EngineResult:
        last = float(close.iloc[i])
        f = float(ema_fast.iloc[i])
        s = float(ema_slow.iloc[i])
        prev_fast = float(ema_fast.iloc[i - self._slope_lookback])
        prev_slow = float(ema_slow.iloc[i - self._slope_lookback])
        evidence = {
            "price_above_ema_fast": bool(last > f),
            "price_above_ema_slow": bool(last > s),
            "ema_fast_above_ema_slow": bool(f > s),
            "ema_fast_slope_positive": bool(f > prev_fast),
            "ema_slow_slope_positive": bool(s > prev_slow),
        }
        direction = self._decide_direction(evidence, last, f, s)
        strength = self._strength(evidence, direction)
        return EngineResult(
            value=f,
            score=int(round(strength)),
            detail=self._describe(direction, strength),
            meta={
                "state": IndicatorState.READY.value,
                "direction": direction.value,
                "strength": round(strength, 2),
                "evidence": evidence,
                "ema_fast": round(f, 6),
                "ema_slow": round(s, 6),
                "ema_fast_slope": round(f - prev_fast, 6),
                "ema_slow_slope": round(s - prev_slow, 6),
            },
        )

    @staticmethod
    def _decide_direction(
        evidence: dict,
        last: float,
        ema_fast: float,
        ema_slow: float,
    ) -> TrendDirection:
        bullish = (
            evidence["ema_fast_above_ema_slow"]
            and evidence["price_above_ema_slow"]
        )
        bearish = (
            not evidence["ema_fast_above_ema_slow"]
            and not evidence["price_above_ema_slow"]
        )
        if bullish:
            return TrendDirection.BULLISH
        if bearish:
            return TrendDirection.BEARISH
        return TrendDirection.NEUTRAL

    def _strength(self, evidence: dict, direction: TrendDirection) -> float:
        """0-100 strength from alignment factors FAND direction."""
        if direction == TrendDirection.UNKNOWN:
            return 0.0
        bullish = direction == TrendDirection.BULLISH
        # Each factor votes toward the trend direction.
        factors = [
            evidence["price_above_ema_fast"] == bullish,
            evidence["price_above_ema_slow"] == bullish,
            evidence["ema_fast_above_ema_slow"],
            evidence["ema_fast_slope_positive"] == bullish,
            evidence["ema_slow_slope_positive"] == bullish,
        ]
        aligned = sum(factors)
        base = aligned / len(factors) * 100.0

        # Neutral direction scores low by construction (mixed alignment).
        if direction == TrendDirection.NEUTRAL:
            return round(base, 2)

        if self._discount_penalty and not evidence["ema_fast_above_ema_slow"]:
            return round(base * 0.6, 2)
        return round(base, 2)

    @staticmethod
    def _describe(direction: TrendDirection, strength: float) -> str:
        if direction == TrendDirection.UNKNOWN:
            return f"Insufficient data (strength {strength})."
        adj = "strong" if strength >= 70 else "moderate" if strength >= 45 else "weak"
        return f"{direction.value} trend ({adj}, strength {strength:.0f})."