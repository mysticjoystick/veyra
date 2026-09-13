"""Analysis engine contracts.

Defines the interface every intelligence engine (trend, structure,
momentum, volume, volatility, regime) must implement. Engines are pure -
they receive a candle frame and return a structured result, with no
I/O or UI dependencies.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import pandas as pd


@dataclass
class EngineResult:
    """Structured output of a single analysis engine step."""

    value: Optional[float] = None          # scalar measurement (e.g. RSI)
    score: int = 0                          # 0-100 component score
    detail: str = ""                        # human-readable rationale
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "value": self.value,
            "score": self.score,
            "detail": self.detail,
            "meta": self.meta,
        }


class AnalysisEngine(ABC):
    """Base class for all per-market intelligence engines."""

    name: str = "engine"

    @abstractmethod
    def analyze(self, df: pd.DataFrame) -> EngineResult:
        """Analyze a candle DataFrame and return an EngineResult.

        df must contain columns: open_time, open, high, low, close, volume,
        sorted ascending by open_time.
        """
        raise NotImplementedError

    @abstractmethod
    def required_columns(self) -> list[str]:
        """Columns this engine requires from the candle frame."""
        raise NotImplementedError

    def warmup_required(self) -> int:
        """Minimum number of candles for a meaningful result.

        Engines should override; the pipeline uses this to compute the
        required lookback for data-quality assessment. Default 0 means
        'no documented requirement'.
        """
        return 0
