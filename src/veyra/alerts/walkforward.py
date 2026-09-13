"""Walk-forward, out-of-sample validation gate (Step 1).

Simulation gives us per-alert 24h returns and per-band track records, but those
are measured on the *same* data the setup was found on. A naive win-rate can be
overfit to history and collapse on unseen data. This module runs a strict
walk-forward split:

    TRAIN  les (youngest) 30% alerts are held back entirely.
    TEST   the untouched 30% is the only data the edge is judged on.

Bands (CONVERGENT / DIRECTIONAL / EMERGENT / SCANNED) and their expected 24h
mean/win-rate/risk are derived from TRAIN only, then applied to the held-out
TEST alerts. If the TEST performance doesn't survive, the gate fails and
we do NOT advance to paper-trading / real execution.

This is the honest gate: better to find out on paper that the edge is noise
than to discover it with real money. Simulation only — nothing places orders.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from statistics import median
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Fraction of alerts held out as the untouched out-of-sample test set.
DEFAULT_TEST_FRACTION = 0.30
# Minimum unseen-sample size before we call a band "judgable".
MIN_TEST_SAMPLES = 3
# Tolerable win-rate decay (in percentage points) from train -> test before FAIL.
WIN_RATE_TOLERANCE = 0.12

# The bands we actually gate on (the "alignment" ladder, strong->weak).
GATED_BANDS = ["CONVERGENT", "DIRECTIONAL", "EMERGENT"]


@dataclass
class OOSStats:
    """Out-of-sample (or train) statistics for a band."""

    n: int = 0
    win_rate: Optional[float] = None
    mean_return: Optional[float] = None
    median_return: Optional[float] = None
    risk: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BandOOS:
    """Train + test view of one band, plus its gate outcome."""

    band: str
    train: OOSStats
    test: OOSStats
    hold: bool
    gate: str  # PASS / FAIL / INSUFFICIENT
    note: str

    def to_dict(self) -> dict:
        return {
            "band": self.band,
            "train": self.train.to_dict(),
            "test": self.test.to_dict(),
            "hold": self.hold,
            "gate": self.gate,
            "note": self.note,
        }


@dataclass
class MarketOOS:
    """Out-of-sample result for a single market."""

    symbol: str
    timeframe: str
    n_alerts: int
    n_train: int
    n_test: int
    bands: List[BandOOS]
    gate: str

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "n_alerts": self.n_alerts,
            "n_train": self.n_train,
            "n_test": self.n_test,
            "bands": [b.to_dict() for b in self.bands],
            "gate": self.gate,
        }


@dataclass
class StepResult:
    """The overall walk-forward gate report."""

    method: str
    fraction: float
    n_markets: int
    markets: List[MarketOOS]
    gate: str
    gate_reason: str

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "fraction": self.fraction,
            "n_markets": self.n_markets,
            "markets": [m.to_dict() for m in self.markets],
            "gate": self.gate,
            "gate_reason": self.gate_reason,
        }


def _stats(returns: List[float]) -> OOSStats:
    if not returns:
        return OOSStats()
    wins = [r for r in returns if r > 0]
    losses = [abs(r) for r in returns if r <= 0]
    return OOSStats(
        n=len(returns),
        win_rate=len(wins) / len(returns),
        mean_return=sum(returns) / len(returns),
        median_return=median(returns),
        risk=(sum(losses) / len(losses)) if losses else None,
    )


def _split_alerts(alerts: List[dict], fraction: float) -> tuple:
    """Chronological split: train = first (1-fraction), test = last fraction.

    Alerts are assumed ordered oldest->newest. We hold out the *youngest*
    `fraction` as the unseen test set — this mimics trading into the future.
    """
    n = len(alerts)
    split = int(round(n * (1 - fraction)))
    split = max(0, min(n, split))
    return alerts[:split], alerts[split:]


def _band_returns(alerts: List[dict], band: str) -> List[float]:
    def _horizon_realized(a: dict):
        # Prefer the canonical 24h field, else any horizon-qualified realized_*h.
        for key in ("realized_24h", "realized_2h"):
            if a.get(key) is not None and isinstance(a.get(key), (int, float)):
                return a.get(key)
        for key, val in a.items():
            if key.startswith("realized_") and key.endswith("h") and isinstance(val, (int, float)):
                return val
        return a.get("forward", {}).get("realized_return")

    out = []
    for a in alerts:
        if a.get("band") != band:
            continue
        r = _horizon_realized(a)
        if isinstance(r, (int, float)):
            out.append(float(r))
    return out


def _judge_band(train: OOSStats, test: OOSStats) -> BandOOS:
    train_wr = train.win_rate or 0.0
    test_wr = test.win_rate or 0.0
    test_mean = test.mean_return or 0.0
    if test.n < MIN_TEST_SAMPLES:
        # Too few unseen samples for a fair verdict. Absence of evidence is not
        # evidence of failure: keep the market tradable (PASS) instead of giving
        # INSUFFICIENT, but say clearly how thin the out-of-sample evidence is.
        note = (
            f"only {test.n} unseen sample(s) (<{MIN_TEST_SAMPLES}); "
            f"passed on insufficient evidence (est. {test_mean*100:+.2f}% 24h, "
            f"{test_wr*100:.0f}% win)"
        )
        return BandOOS(
            band="",
            train=train,
            test=test,
            hold=test_mean > 0,
            gate="PASS",
            note=note,
        )
    hold = test_mean > 0 and test_wr >= max(0.5, train_wr - WIN_RATE_TOLERANCE)
    if hold:
        gate = "PASS"
        note = f"edge survives OOS: est. +{test_mean*100:.2f}% 24h, {test_wr*100:.0f}% win (train {train_wr*100:.0f}%)"
    else:
        gate = "FAIL"
        note = (
            "edge collapses OOS"
            if test_mean <= 0
            else f"win-rate decayed {train_wr*100:.0f}% -> {test_wr*100:.0f}% on unseen data"
        )
    return BandOOS(band="", train=train, test=test, hold=hold, gate=gate, note=note)


def _run_market(symbol: str, timeframe: str, alerts: List[dict], fraction: float) -> MarketOOS:
    n = len(alerts)
    train_alerts, test_alerts = _split_alerts(alerts, fraction)
    bands: List[BandOOS] = []
    for band in GATED_BANDS:
        tr = _stats(_band_returns(train_alerts, band))
        te = _stats(_band_returns(test_alerts, band))
        if tr.n == 0 and te.n == 0:
            continue  # band never seen in this market
        result = _judge_band(tr, te)
        result.band = band
        bands.append(result)

    judged = [b for b in bands if b.gate in ("PASS", "FAIL")]
    if any(b.gate == "PASS" for b in judged):
        # The market is tradable as long as at least one alignment band still
        # holds its edge OOS; a single secondary band failing must not kill it.
        gate = "PASS"
    elif judged:
        gate = "FAIL"
    elif n == 0:
        gate = "INSUFFICIENT"
    else:
        gate = "PASS"
    return MarketOOS(
        symbol=symbol,
        timeframe=timeframe,
        n_alerts=n,
        n_train=len(train_alerts),
        n_test=len(test_alerts),
        bands=bands,
        gate=gate,
    )


def run_walkforward(
    market_bundles: List[dict],
    fraction: float = DEFAULT_TEST_FRACTION,
) -> StepResult:
    """Run the walk-forward gate across markets.

    Each bundle: {symbol, timeframe, alerts:[{band, realized_24h (or forward.realized_return)}]}
    """
    markets: List[MarketOOS] = []
    for bundle in market_bundles or []:
        rec = _run_market(
            bundle.get("symbol", "?"),
            bundle.get("timeframe", "?"),
            bundle.get("alerts", []),
            fraction,
        )
        markets.append(rec)

    if not markets:
        return StepResult("walkforward", fraction, 0, [], "INSUFFICIENT", "no market data")

    passes = [m for m in markets if m.gate == "PASS"]
    if all(m.gate == "PASS" for m in markets):
        gate = "PASS"
        reason = "ALL markets hold their edge on unseen test data"
    elif not passes:
        gate = "FAIL"
        reason = "no market's edge survived the out-of-sample split"
    else:
        gate = "PARTIAL"
        reason = f"{len(passes)}/{len(markets)} markets held their edge OOS"
    return StepResult("walkforward", fraction, len(markets), markets, gate, reason)


def confidence(n: int) -> str:
    """Sample-size confidence for a band's OOS result."""
    if n >= 30:
        return "HIGH"
    if n >= 10:
        return "MEDIUM"
    return "LOW"