"""Deterministic fake provider and e2e test helpers (no live network)."""

from __future__ import annotations

from typing import List, Optional

from veyra.data.provider.base import MarketDataProvider
from veyra.domain.candle import Candle
from veyra.error import ProviderRequestError

from .helpers import make_candles


class FakeProvider(MarketDataProvider):
    """Serves a fixed catalog of candles within a range, deterministically.

    Optionally simulates a provider failure by raising ProviderRequestError
    on the first N calls, and can drop a subset (to create a gap).
    """

    name = "fake"
    version = "fake-v1"

    def __init__(self, catalog: List[Candle], fail_first: int = 0) -> None:
        self.catalog = sorted(catalog, key=lambda c: c.open_time)
        self.fail_first = fail_first
        self.calls = 0

    def is_symbol_supported(self, symbol: str, timeframe: str) -> bool:
        return True

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[Candle]:
        self.calls += 1
        if self.fail_first > 0:
            self.fail_first -= 1
            raise ProviderRequestError("simulated provider failure")
        lo = start_time if start_time is not None else None
        hi = end_time if end_time is not None else None
        out = [c for c in self.catalog if (lo is None or c.open_time >= lo) and (hi is None or c.open_time <= hi)]
        # Reuse catalog but stamp requested symbol/timeframe (service re-stamps).
        return list(out)


def make_fake_provider(
    symbol: str = "BTC/USDT",
    timeframe: str = "4H",
    n: int = 40,
    start_ts: int = 1_000_000,
    fail_first: int = 0,
) -> FakeProvider:
    return FakeProvider(
        make_candles(
            symbol=symbol,
            timeframe=timeframe,
            n=n,
            start_ts=start_ts,
        ),
        fail_first=fail_first,
    )