"""Volume engine.

Deterministic volume analysis:
- current volume vs rolling average (relative volume)
- volume expansion / contraction / normal
- price-volume confirmation (direction of price move supported by volume)

All periods configurable. Thresholds for expansion/contraction are initial
hypotheses, documented and subject to later validation.
"""

from __future__ import annotations

from typing import List

import pandas as pd

from ..domain import IndicatorState, VolumeState
from .engine import AnalysisEngine, EngineResult


class VolumeEngine(AnalysisEngine):
    name = "volume"

    def __init__(
        self,
        volume_ma_period: int = 20,
        expansion_ratio: float = 1.5,
        contraction_ratio: float = 0.7,
    ) -> None:
        self._ma = volume_ma_period
        self._expand = expansion_ratio
        self._contract = contraction_ratio

    def required_columns(self) -> list[str]:
        return ["close", "volume"]

    def warmup_required(self) -> int:
        return self._ma + 5

    def analyze(self, df: pd.DataFrame) -> EngineResult:
        n = len(df)
        if n < self.warmup_required():
            return EngineResult(
                value=None,
                score=0,
                detail=f"Insufficient data: need >={self.warmup_required()} candles, got {n}",
                meta={
                    "state": IndicatorState.INSUFFICIENT_DATA.value,
                    "volume_state": VolumeState.UNKNOWN.value,
                    "required": self.warmup_required(),
                    "actual": n,
                },
            )

        volume = df["volume"]
        close = df["close"]

        cur = float(volume.iloc[-1])
        avg = float(volume.rolling(self._ma).mean().iloc[-1])
        rel_vol = cur / avg if avg > 0 else None

        if rel_vol is None:
            vol_state = VolumeState.UNKNOWN
        elif rel_vol >= self._expand:
            vol_state = VolumeState.EXPANDING
        elif rel_vol <= self._contract:
            vol_state = VolumeState.CONTRACTING
        else:
            vol_state = VolumeState.NORMAL

        confirmation = self._price_volume_confirmation(df)
        evidence = {
            "current_volume": cur,
            "average_volume": avg,
            "relative_volume": round(rel_vol, 4) if rel_vol is not None else None,
            "price_volume_confirmation": confirmation,
        }

        score = self._volume_score(rel_vol, confirmation)

        return EngineResult(
            value=rel_vol,
            score=score,
            detail=self._describe(vol_state, rel_vol, confirmation),
            meta={
                "state": IndicatorState.READY.value,
                "volume_state": vol_state.value,
                "relative_volume": round(rel_vol, 4) if rel_vol is not None else None,
                "price_volume_confirmation": confirmation,
                "evidence": evidence,
            },
        )

    @staticmethod
    def _price_volume_confirmation(df: pd.DataFrame) -> bool:
        """True if the last bar's price direction is supported by relative
        volume above 1.0 (up move with volume = confirmation)."""
        if len(df) < 2:
            return False
        last = df.iloc[-1]
        prev = df.iloc[-2]
        avg = df["volume"].rolling(20).mean().iloc[-1]
        if avg <= 0:
            return False
        rel = float(last["volume"]) / float(avg)
        direction_up = bool(last["close"] > prev["close"])
        direction_down = bool(last["close"] < prev["close"])
        if direction_up:
            return rel >= 1.0
        if direction_down:
            # For a down move, elevated volume confirms selling pressure too.
            return rel >= 1.0
        return False

    @staticmethod
    def _volume_score(rel_vol, confirmation: bool) -> int:
        if rel_vol is None:
            return 0
        # Base on relative volume magnitude; confirmation adds a bonus.
        score = min(100.0, rel_vol / 2.0 * 100.0)
        if confirmation:
            score = min(100.0, score + 15.0)
        return int(round(score))

    @staticmethod
    def _describe(vol_state: VolumeState, rel_vol, confirmation: bool) -> str:
        rv = f"{rel_vol:.2f}" if rel_vol is not None else "N/A"
        return (
            f"Volume {vol_state.value} (rel vol {rv}, "
            f"price-vol confirm={confirmation})."
        )

    # -- Progressive (per-index) path --------------------------------------

    def analyze_each(self, df: pd.DataFrame) -> "List[EngineResult]":
        """One causal pass; index i is bit-identical to ``analyze(df[:i+1])``."""
        n = len(df)
        if n == 0:
            return []
        wu = self.warmup_required()
        volume = df["volume"]
        close = df["close"]
        avg = volume.rolling(self._ma).mean()
        # Confirmation uses a hard-coded 20-bar volume MA (same as the single-bar
        # path); precompute so per-index work is O(1), not O(N).
        avg20 = volume.rolling(20).mean()
        out: list = []
        insuff = {
            "state": IndicatorState.INSUFFICIENT_DATA.value,
            "volume_state": VolumeState.UNKNOWN.value,
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
            confirmation = self._confirm_at(close, volume, avg20, i)
            out.append(self._build_volume_result(volume, avg, i, confirmation))
        return out

    @staticmethod
    def _confirm_at(close, volume, avg20, i: int) -> bool:
        if i < 1:
            return False
        a20 = avg20.iloc[i]
        if a20 <= 0:
            return False
        rel = float(volume.iloc[i]) / float(a20)
        up = bool(float(close.iloc[i]) > float(close.iloc[i - 1]))
        down = bool(float(close.iloc[i]) < float(close.iloc[i - 1]))
        if up or down:
            return rel >= 1.0
        return False

    def _build_volume_result(self, volume, avg, i: int, confirmation: bool) -> EngineResult:
        cur = float(volume.iloc[i])
        avg_val = float(avg.iloc[i])
        rel_vol = cur / avg_val if avg_val > 0 else None

        if rel_vol is None:
            vol_state = VolumeState.UNKNOWN
        elif rel_vol >= self._expand:
            vol_state = VolumeState.EXPANDING
        elif rel_vol <= self._contract:
            vol_state = VolumeState.CONTRACTING
        else:
            vol_state = VolumeState.NORMAL

        evidence = {
            "current_volume": cur,
            "average_volume": avg_val,
            "relative_volume": round(rel_vol, 4) if rel_vol is not None else None,
            "price_volume_confirmation": confirmation,
        }
        score = self._volume_score(rel_vol, confirmation)
        return EngineResult(
            value=rel_vol,
            score=score,
            detail=self._describe(vol_state, rel_vol, confirmation),
            meta={
                "state": IndicatorState.READY.value,
                "volume_state": vol_state.value,
                "relative_volume": round(rel_vol, 4) if rel_vol is not None else None,
                "price_volume_confirmation": confirmation,
                "evidence": evidence,
            },
        )