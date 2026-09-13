"""24-hour opportunity model: realized track record, projection, and P&L.

Veyra's realtime idea — "is there a profitable move over the next 24h, and
roughly how much would I make for the amount I'm trading?" — is answered here
with three separate, deliberately-honest numbers:

  1. REALIZED — what a setup's price actually did over the 24h after it fired.
     This is a fact measured from the stored candles, not a guess.

  2. PROJECTED — the model's expectation for a similar setup over 24h, taken as
     the historical mean/win-rate of that clarity band. This is a *projection*
     is NOT a promise; your own history shows aligned setups both win and lose.

  3. P&L — amount you're trading multiplied by the projected (and separately by
     the realized) 24h return, to give a dollar figure framed as estimate-only,
     never guaranteed.

Every number uses direction-aware returns (LONG: up is profit; SHORT: down is
profit). Simulation + estimation only — nothing here places or executes a trade.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Dict, List, Optional

import pandas as pd

# Window (seconds) defining the default "24h opportunity" horizon.
HORIZON_24H_S = 24 * 3600

# Optional per-timeframe horizon override (seconds). Short timeframes settle
# fast so a real track record accumulates quickly; 4H/1D keep the 24h edge.
HORIZON_BY_TIMEFRAME: Dict[str, int] = {
    "3m": 2 * 3600,
    "5m": 2 * 3600,
    "15m": 2 * 3600,
}


def horizon_for(timeframe: str) -> int:
    """Settle/projection horizon (seconds) for a timeframe."""
    return HORIZON_BY_TIMEFRAME.get(timeframe or "", HORIZON_24H_S)


def _horizon_hours(horizon: int) -> str:
    h = int(round(horizon / 3600))
    return "24" if h <= 0 else str(h)


def _field(prefix: str, horizon: int) -> str:
    """Return a horizon-qualified per-alert field, e.g. `net_2h` / `realized_24h`."""
    return f"{prefix}_{_horizon_hours(horizon)}h"


@dataclass
class ForwardStats:
    """Aggregated forward-return statistics for a clarity band."""

    band: str
    n: int = 0
    win_rate: Optional[float] = None   # fraction of returns > 0 (0..1)
    mean_return: Optional[float] = None  # mean direction-aware 24h return
    median_return: Optional[float] = None
    risk: Optional[float] = None       # mean magnitude of losing (<=0) returns

    def to_dict(self) -> dict:
        return {
            "band": self.band,
            "n": self.n,
            "win_rate": _fmt(self.win_rate),
            "mean_return": _fmt(self.mean_return),
            "median_return": _fmt(self.median_return),
            "risk": _fmt(self.risk),
        }


@dataclass
class Projection:
    """A 24h projection for a single alert, plus a derived P&L estimate."""

    band: str
    sample_size: int
    win_rate: Optional[float]
    projected_return: Optional[float]   # central expected 24h return (fraction)
    pnl_at_amount: Optional[float]      # amount * projected_return
    realized_return: Optional[float]    # what actually happened (may be None)
    realized_pnl_at_amount: Optional[float]
    confidence: str                     # LOW / MEDIUM / HIGH (by sample size)

    def to_dict(self) -> dict:
        return {
            "band": self.band,
            "sample_size": self.sample_size,
            "win_rate": _fmt(self.win_rate),
            "projected_return": _fmt(self.projected_return),
            "pnl_at_amount": _fmt(self.pnl_at_amount),
            "realized_return": _fmt(self.realized_return),
            "realized_pnl_at_amount": _fmt(self.realized_pnl_at_amount),
            "confidence": self.confidence,
        }


class ForwardReturnModel:
    """Computes realized + projected 24h returns across stored candle data."""

    def __init__(self, candles: Optional[pd.DataFrame] = None, horizon: Optional[int] = None) -> None:
        # Candles must be sorted ascending by open_time.
        self.horizon = horizon or HORIZON_24H_S
        if candles is None or candles.empty:
            self._df = pd.DataFrame()
            self._times: List[int] = []
        else:
            self._df = candles.sort_values("open_time").reset_index(drop=True)
            self._times = self._df["open_time"].tolist()

    # -- Realized -----------------------------------------------------------

    def realized(
        self,
        detection_ts: int,
        side: str,
        entry_price: Optional[float] = None,
        horizon: Optional[int] = None,
    ) -> Optional[float]:
        """Direction-aware return over the horizon after `detection_ts`.

        Uses the close of the first candle at/after the horizon relative to
        the entry price (defaults to the close of the detection bar). Returns
        None when there isn't enough forward data to measure a full horizon.
        """
        if self._df.empty or entry_price is None or entry_price <= 0:
            return None
        ref = float(entry_price)
        # Entry mark: use entry_price as the base.
        exit_price = self._price_after(detection_ts, horizon or self.horizon)
        if exit_price is None:
            return None
        raw = (float(exit_price) - ref) / ref
        return _directional(side, raw)

    def _price_after(self, ts: int, horizon: int) -> Optional[float]:
        """Close of the first candle opening at/after ts+horizon, else None."""
        target = ts + horizon
        if not self._times:
            return None
        # last candle close if past the end of available history
        if target > self._times[-1]:
            return None
        row = self._row_at_or_after(target)
        return float(row["close"]) if row is not None else None

    def _row_at_or_after(self, ts: int) -> Optional[pd.Series]:
        import bisect

        idx = bisect.bisect_left(self._times, ts)
        if idx >= len(self._df):
            return None
        return self._df.iloc[idx]

    # -- Aggregation --------------------------------------------------------

    def stats_by_band(self, alerts: List[dict], field: str = "realized_24h") -> Dict[str, ForwardStats]:
        """Aggregate 24h returns per clarity band across alerts.

        `field` selects which per-alert return to aggregate — "realized_24h"
        (gross, default) or "net_24h" (after trading costs).
        """
        groups: Dict[str, List[float]] = {}
        for a in alerts or []:
            r = a.get(field)
            if r is None and field == "realized_24h":
                r = a.get("forward", {}).get("realized_return")
            if r is None:
                continue
            groups.setdefault(a.get("band", "SCANNED"), []).append(r)

        out: Dict[str, ForwardStats] = {}
        for band, returns in groups.items():
            wins = [r for r in returns if r > 0]
            losses = [abs(r) for r in returns if r <= 0]
            out[band] = ForwardStats(
                band=band,
                n=len(returns),
                win_rate=len(wins) / len(returns) if returns else None,
                mean_return=sum(returns) / len(returns) if returns else None,
                median_return=median(returns) if returns else None,
                risk=(sum(losses) / len(losses)) if losses else None,
            )
        return out

    def find_band_stats(
        self, band: str, stats: Dict[str, ForwardStats]
    ) -> Optional[ForwardStats]:
        return stats.get(band)

    # -- Projection + P&L ---------------------------------------------------

    def project(
        self,
        alert: dict,
        stats: Dict[str, ForwardStats],
        amount: Optional[float] = None,
    ) -> Projection:
        """Project a 24h return from the band's history and derive a P&L.

        The central projection is the band's mean realized 24h return. Confidence
        is set from sample size: >=30 HIGH, >=10 MEDIUM, else LOW. P&L is simply
        `amount * projection` — an estimate, not a guarantee.
        """
        band = alert.get("band", "SCANNED")
        st = self.find_band_stats(band, stats)
        proj = st.mean_return if st and st.n else None
        sample = st.n if st else 0

        if st is None or sample == 0:
            confidence = "LOW"
        elif sample >= 30:
            confidence = "HIGH"
        elif sample >= 10:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        amt = float(amount) if amount else None

        return Projection(
            band=band,
            sample_size=sample,
            win_rate=st.win_rate if st else None,
            projected_return=proj,
            pnl_at_amount=(amt * proj) if (amt is not None and proj is not None) else None,
            realized_return=None,
            realized_pnl_at_amount=None,
            confidence=confidence,
        )


def _directional(side: str, raw: float) -> float:
    """Sign a raw price change by trade side (LONG: up is profit, SHORT: down)."""
    return raw if side == "LONG" else -raw


def _fmt(x: Optional[float]) -> Optional[float]:
    return round(x, 6) if x is not None else None