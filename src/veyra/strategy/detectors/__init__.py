"""Setup detectors. Each detector is a pure rule set over a MarketSnapshot."""

from __future__ import annotations

from typing import List

from .base import SetupDetector
from .breakout import BreakoutDetector
from .breakout_retest import BreakoutRetestDetector
from .pullback import PullbackDetector
from .range_rejection import RangeRejectionDetector
from .trend_continuation import TrendContinuationDetector

__all__ = [
    "SetupDetector",
    "BreakoutDetector",
    "BreakoutRetestDetector",
    "PullbackDetector",
    "RangeRejectionDetector",
    "TrendContinuationDetector",
]


def default_detectors() -> List[SetupDetector]:
    """Return the default set of detectors for Phase 3."""
    return [
        TrendContinuationDetector(),
        PullbackDetector(),
        BreakoutDetector(),
        BreakoutRetestDetector(),
        RangeRejectionDetector(),
    ]