"""Portfolio-level risk layer (checked before any entry executes).

The per-market lenses say nothing about cross-market concentration: regime gates
can fire on correlated markets simultaneously (e.g. majors trending together).
This layer is the first place Veyra looks across its own book.

Checks, applied to a *candidate* entry against the currently open positions:

  1. Per-market cooldown    : never stack a second entry on the same
                              (symbol, timeframe) while one is already open,
                              even if a different band independently qualifies.
  2. Max concurrent exposure: hard cap on total open notional across all
                              markets as a fraction of account equity. As the
                              book approaches the cap, the candidate's size is
                              scaled down; at the cap it is rejected.
  3. Correlation            : if the candidate's market is correlated
                              (>= ``correlation_threshold``) with an already-open
                              position in the SAME direction, size is reduced by
                              ``correlation_multiplier`` (both would move together).

Order of precedence is fixed: cooldown (reject) > exposure (reject or scale) >
correlation (scale). All reasons are surfaced to the caller
(``PortfolioCheckResult.reason``) so a skipped entry is auditable, not silent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd

from ..config import Settings, get_settings
from ..data.candle_store import CandleStore
from ..data.timeframe import timeframe_interval_seconds


@dataclass(frozen=True)
class PortfolioCheckResult:
    approved: bool
    size_multiplier: float = 1.0
    reason: str = ""

    @classmethod
    def ok(cls, multiplier: float = 1.0) -> "PortfolioCheckResult":
        return cls(approved=True, size_multiplier=multiplier, reason="portfolio_ok")

    @classmethod
    def reject(cls, reason: str) -> "PortfolioCheckResult":
        return cls(approved=False, size_multiplier=0.0, reason=reason)


class PortfolioRiskPolicy:
    """Deterministic, configured cross-market risk rules (read-only, stateless)."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        s = settings or get_settings()
        self._exposure_pct = s.portfolio_max_exposure_pct
        self._correlation_threshold = s.portfolio_correlation_threshold
        self._correlation_multiplier = s.portfolio_correlation_multiplier
        self._equity = s.portfolio_equity

    # -- cross-market book helpers ----------------------------------------

    @staticmethod
    def open_book(ledgers: List[object]) -> List[dict]:
        """Flatten the currently-open positions of many ledgers into rows.

        Each ledger is expected to expose ``unclosed()`` returning positions with
        ``symbol``, ``timeframe``, ``side``, ``notional``.
        """
        rows: List[dict] = []
        for ledger in ledgers:
            for p in ledger.unclosed():
                rows.append(
                    {
                        "symbol": getattr(p, "symbol", ""),
                        "timeframe": getattr(p, "timeframe", ""),
                        "side": getattr(p, "side", ""),
                        "notional": float(getattr(p, "notional") or 0.0),
                    }
                )
        return rows

    def compute_correlations(
        self,
        datasets: List[Dict[str, str]],
        store: Optional[object] = None,
        days: int = 30,
    ) -> Dict[str, Dict[str, float]]:
        """Trailing cross-market correlation of DAILY closes over ``days``.

        Every market is resampled to a common daily grid so 4H and 1D series are
        directly comparable. Returns {market: {other_market: r}} using the
        canonical "BTC/USDT" symbol form; markets without enough history are
        omitted from keys but never fault.
        """
        store = store or CandleStore(get_settings())
        daily: Dict[str, pd.Series] = {}
        for ds in datasets:
            symbol, timeframe = ds["symbol"], ds["timeframe"]
            try:
                df = store.load(symbol, timeframe)
            except Exception:  # noqa: BLE001 - one market must not fault the book
                continue
            if df is None or len(df) < 3:
                continue
            interval = timeframe_interval_seconds(timeframe)
            tail = df.tail(int(days * 86400 / interval) + 2)
            prices = pd.Series(
                tail["close"].values,
                index=pd.to_datetime(tail["open_time"].values, unit="s"),
            )
            daily[symbol] = prices.resample("1D").last().pct_change().dropna()

        if len(daily) < 2:
            return {}
        frame = pd.DataFrame(daily).dropna(how="all")
        min_periods = max(1, min(10, len(frame)))
        corr = frame.corr(min_periods=min_periods)
        out: Dict[str, Dict[str, float]] = {}
        for market in corr.columns:
            row = {
                other: float(corr.loc[market, other])
                for other in corr.columns
                if other != market and pd.notna(corr.loc[market, other])
            }
            out[market] = row
        return out

    # -- the gate ---------------------------------------------------------

    def check(
        self,
        *,
        symbol: str,
        timeframe: str,
        side: str,
        notional: float,
        open_positions: List[dict],
        correlations: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> PortfolioCheckResult:
        """Evaluate a candidate entry against the current open book."""
        # 1. Per-market cooldown: no stacking the same (symbol, timeframe).
        for p in open_positions:
            if p["symbol"] == symbol and p["timeframe"] == timeframe:
                return PortfolioCheckResult.reject("market_cooldown")

        # 2. Max concurrent exposure (hard cap on total open notional).
        current = sum(float(p.get("notional") or 0.0) for p in open_positions)
        cap = self._equity * self._exposure_pct
        usable = cap - current
        if usable <= 0 and notional > 0:
            return PortfolioCheckResult.reject("portfolio_exposure_cap")
        exposure_mult = 1.0
        if notional > usable:
            exposure_mult = max(0.0, usable / notional)

        # 3. Correlation: same-direction correlated book -> reduce size.
        corr_mult = 1.0
        if correlations:
            correlated = correlations.get(symbol, {})
            for p in open_positions:
                r = correlated.get(p["symbol"])
                if (
                    r is not None
                    and r >= self._correlation_threshold
                    and p["side"] == side
                ):
                    corr_mult = min(corr_mult, self._correlation_multiplier)

        multiplier = exposure_mult * corr_mult
        if multiplier <= 0:
            return PortfolioCheckResult.reject("portfolio_exposure_cap")
        return PortfolioCheckResult.ok(multiplier)