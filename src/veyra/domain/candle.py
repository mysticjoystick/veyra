"""Candle domain model.

Represents a single OHLCV candle. Independent of the storage engine used
to persist it. Timestamps are timezone-aware UTC seconds.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict


@dataclass(frozen=True)
class Candle:
    symbol: str
    timeframe: str
    open_time: int  # epoch seconds, UTC
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def is_valid(self) -> bool:
        """Basic sanity checks for a single candle."""
        if self.high < self.low:
            return False
        if min(self.open, self.close) < self.low:
            return False
        if max(self.open, self.close) > self.high:
            return False
        if self.volume < 0:
            return False
        return True
