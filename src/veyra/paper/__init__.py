"""Phase 5 paper trading (simulation-only) package.

Provides an offline paper account that replays the frozen Phase 4 baseline
forward over real candles with a virtual account. Never executes real orders.
"""

from .engine import (
    REAL_EXECUTION_FORBIDDEN,
    PaperEngine,
    PaperSession,
    PaperTrade,
)

__all__ = [
    "REAL_EXECUTION_FORBIDDEN",
    "PaperEngine",
    "PaperSession",
    "PaperTrade",
]