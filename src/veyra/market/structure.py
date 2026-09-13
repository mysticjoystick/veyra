"""Market structure engine.

Detects swing highs/lows and classifies structure with explicit rules.

Swing definition (documented):
A pivot is a point where a central bar is the highest/lowest among `left`
bars before it and `right` bars after it. Using a fixed lookback `k` makes
structure deterministic and reproducible. Swing detection necessarily
requires `right` future bars to confirm a pivot, which the *backtester*
must handle by only using pivots confirmed at decision time (see note in
`analyze`). For description purposes we detect pivots over the whole series.

Structural labels (last completed swing sequence):
- HH_HL  = higher highs and higher lows (bullish)
- LH_LL  = lower highs and lower lows (bearish)
- HH_LL  = higher highs but lower lows (expansion/divergence)
- LH_HL  = lower highs but higher lows (tightening/range)
- NEUTRAL= neither clear via distinct swing points

Break of structure: an HH_HL structure followed by a close above the last
swing high (BOS up); an LH_LL structure followed by a close below the last
swing low (BOS down). A failed break is a swing that closes beyond a pivot
but fails to continue (detected via reversal back across).

No look-ahead bias: `analyze` labels structure based only on pivots up to
the final bar; it never uses future data to label the *present*. Each
classification is reproducible from stored candles.
"""

from __future__ import annotations

from typing import List

import pandas as pd

from ..domain import IndicatorState, StructureAction, StructureState
from .engine import AnalysisEngine, EngineResult


class StructureEngine(AnalysisEngine):
    name = "structure"

    def __init__(
        self,
        pivot_lookback: int = 3,
        min_pivots: int = 3,
        use_close_for_break: bool = True,
    ) -> None:
        self._k = pivot_lookback
        self._min_pivots = min_pivots
        self._use_close = use_close_for_break
        # Total bars needed: lookback either side plus at least a couple bars.
        self._warmup = 2 * pivot_lookback + min_pivots

    def required_columns(self) -> list[str]:
        return ["open", "high", "low", "close"]

    def warmup_required(self) -> int:
        return self._warmup

    def analyze(self, df: pd.DataFrame) -> EngineResult:
        n = len(df)
        if n < self._warmup:
            return EngineResult(
                value=None,
                score=0,
                detail=f"Insufficient data: need >={self._warmup} candles, got {n}",
                meta={
                    "state": IndicatorState.INSUFFICIENT_DATA.value,
                    "structure": StructureState.UNKNOWN.value,
                    "action": StructureAction.NONE.value,
                    "required": self._warmup,
                    "actual": n,
                },
            )

        highs = df["high"].to_numpy()
        lows = df["low"].to_numpy()
        closes = df["close"].to_numpy()

        swing_highs = self._find_pivots(highs, self._k, pivot_type="high")
        swing_lows = self._find_pivots(lows, self._k, pivot_type="low")

        structure = self._classify_structure(swing_highs, swing_lows, highs, lows)
        action, break_level = self._detect_action(
            highs, lows, closes, swing_highs, swing_lows, structure
        )

        evidence = {
            "swing_highs": [round(float(highs[i]), 6) for i in swing_highs],
            "swing_lows": [round(float(lows[i]), 6) for i in swing_lows],
            "swing_high_indices": list(swing_highs),
            "swing_low_indices": list(swing_lows),
            "pivot_lookback": self._k,
        }

        score = self._structure_score(structure)

        return EngineResult(
            value=closes[-1],
            score=score,
            detail=self._describe(structure, action),
            meta={
                "state": IndicatorState.READY.value,
                "structure": structure.value,
                "action": action.value,
                "evidence": evidence,
                "last_swing_high": (
                    round(float(highs[swing_highs[-1]]), 6) if swing_highs else None
                ),
                "last_swing_low": (
                    round(float(lows[swing_lows[-1]]), 6) if swing_lows else None
                ),
                "break_level": (
                    round(float(break_level), 6) if break_level is not None else None
                ),
            },
        )

    # -- Swing identification ----------------------------------------------

    @staticmethod
    def _find_pivots(series, k: int, pivot_type: str):
        """Return indices of pivot points (local extrema) with lookback k."""
        pivots = []
        n = len(series)
        for i in range(k, n - k):
            window_hi = series[i - k : i + k + 1]
            if pivot_type == "high" and series[i] == window_hi.max():
                # require strict left comparison to avoid flat duplicates
                if series[i] > series[i - k : i].max():
                    pivots.append(i)
            elif pivot_type == "low" and series[i] == window_hi.min():
                if series[i] < series[i - k : i].min():
                    pivots.append(i)
        return pivots

    # -- Classification -----------------------------------------------------

    def _classify_structure(
        self, swing_highs, swing_lows, highs, lows
    ) -> StructureState:
        """Classify based on the most recent logged swing highs and lows.

        Rules (explicit, deterministic). `swing_highs`/`swing_lows` are pivot
        *indices*; we compare the price levels at those pivots (not the index
        positions, which always increase with time).
          - Need >= 2 logged swing highs and >= 2 logged swing lows.
          - higher_high  : last swing high level > previous swing high level
          - higher_low   : last swing low  level > previous swing low level
          - lower_high   : last swing high < previous swing high
          - lower_low    : last swing low  < previous swing low
        Combinations map to structure states.
        """
        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return StructureState.NEUTRAL

        hh = highs[swing_highs[-1]] > highs[swing_highs[-2]]
        hl = lows[swing_lows[-1]] > lows[swing_lows[-2]]

        if hh and hl:
            return StructureState.HIGHER_HIGHS_HIGHER_LOWS
        if (not hh) and (not hl):
            return StructureState.LOWER_HIGHS_LOWER_LOWS
        if hh and (not hl):
            return StructureState.HIGHER_HIGHS_LOWER_LOWS
        return StructureState.LOWER_HIGHS_HIGHER_LOWS

    def _detect_action(
        self,
        highs,
        lows,
        closes,
        swing_highs,
        swing_lows,
        structure: StructureState,
    ):
        """Return (StructureAction, break_level)."""
        last_close = closes[-1]

        if structure == StructureState.HIGHER_HIGHS_HIGHER_LOWS and swing_highs:
            last_sh = swing_highs[-1]
            if self._use_close and last_close > highs[last_sh]:
                return StructureAction.BREAK_OF_STRUCTURE_UP, highs[last_sh]
            if not self._use_close and last_close >= highs[last_sh] - 1e-12:
                return StructureAction.BREAK_OF_STRUCTURE_UP, None

        if structure == StructureState.LOWER_HIGHS_LOWER_LOWS and swing_lows:
            last_sl = swing_lows[-1]
            if self._use_close and last_close < lows[last_sl]:
                return StructureAction.BREAK_OF_STRUCTURE_DOWN, lows[last_sl]
            if not self._use_close and last_close <= lows[last_sl] + 1e-12:
                return StructureAction.BREAK_OF_STRUCTURE_DOWN, None

        return StructureAction.NONE, None

    @staticmethod
    def _structure_score(structure: StructureState) -> int:
        """A simple 0-100 alignment score (not a probability)."""
        mapping = {
            StructureState.HIGHER_HIGHS_HIGHER_LOWS: 90,
            StructureState.LOWER_HIGHS_LOWER_LOWS: 85,
            StructureState.HIGHER_HIGHS_LOWER_LOWS: 50,
            StructureState.LOWER_HIGHS_HIGHER_LOWS: 45,
            StructureState.NEUTRAL: 40,
            StructureState.UNKNOWN: 0,
        }
        return mapping[structure]

    @staticmethod
    def _describe(structure: StructureState, action: StructureAction) -> str:
        parts = [f"Structure: {structure.value}"]
        if action != StructureAction.NONE:
            parts.append(f"Action: {action.value}")
        return " | ".join(parts)

    # -- Progressive (per-index) path --------------------------------------

    def analyze_each(self, df: pd.DataFrame) -> "List[EngineResult]":
        """One causal pass; index i is bit-identical to ``analyze(df[:i+1])``.

        A pivot at index p is only *confirmed* once ``p + k <= i`` (k future
        bars are inside the prefix). We precompute all candidate pivots over the
        full frame once (``_find_pivots`` over ``[k, n-k)`` captures every pivot
        that can ever be confirmed), then at each index i consider only those
        with ``p <= i - k``. This reproduces ``_find_pivots`` on each prefix in
        O(1) amortised per index.
        """
        n = len(df)
        if n == 0:
            return []
        wu = self.warmup_required()
        highs = df["high"].to_numpy()
        lows = df["low"].to_numpy()
        closes = df["close"].to_numpy()
        all_highs = self._find_pivots(highs, self._k, "high")
        all_lows = self._find_pivots(lows, self._k, "low")

        out: list = []
        insuff = {
            "state": IndicatorState.INSUFFICIENT_DATA.value,
            "structure": StructureState.UNKNOWN.value,
            "action": StructureAction.NONE.value,
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
            # Confirmed as of index i: pivot p with p + k <= i.
            swing_highs = [p for p in all_highs if p <= i - self._k]
            swing_lows = [p for p in all_lows if p <= i - self._k]
            out.append(
                self._build_structure_result_df(
                    highs, lows, closes, swing_highs, swing_lows, i
                )
            )
        return out

    def _build_structure_result_df(
        self, highs, lows, closes, swing_highs, swing_lows, i: int
    ) -> EngineResult:
        slice_len = i + 1
        structure = self._classify_structure(swing_highs, swing_lows, highs, lows)
        action, break_level = self._detect_action(
            highs[:slice_len], lows[:slice_len], closes[:slice_len],
            swing_highs, swing_lows, structure,
        )
        evidence = {
            "swing_highs": [round(float(highs[idx]), 6) for idx in swing_highs],
            "swing_lows": [round(float(lows[idx]), 6) for idx in swing_lows],
            "swing_high_indices": list(swing_highs),
            "swing_low_indices": list(swing_lows),
            "pivot_lookback": self._k,
        }
        score = self._structure_score(structure)
        return EngineResult(
            value=closes[i],
            score=score,
            detail=self._describe(structure, action),
            meta={
                "state": IndicatorState.READY.value,
                "structure": structure.value,
                "action": action.value,
                "evidence": evidence,
                "last_swing_high": (
                    round(float(highs[swing_highs[-1]]), 6) if swing_highs else None
                ),
                "last_swing_low": (
                    round(float(lows[swing_lows[-1]]), 6) if swing_lows else None
                ),
                "break_level": (
                    round(float(break_level), 6) if break_level is not None else None
                ),
            },
        )