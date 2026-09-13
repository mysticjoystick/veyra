"""Live efficiency score (0-100) for alert framing.

Combines two sources of real, accumulated evidence into a single adaptive
score that gets *stronger as live trades complete*:

* `band_track`  - the band's realized forward track record (win-rate,
  expectancy vs. downside). This is the stable prior sampled from history.
* `live_trades` - actual closed trades from the live paper ledger. This is
  the accumulating, market-specific evidence.

The score weights the live evidence more heavily as its sample grows, so a
market that genuinely improves trade-over-trade lifts the number — but it never
fabricates certainty: with no live trades it falls back to the historical band
prior, and low samples report LOW confidence.

Aims to be honest: the number is an *evidence-weighted* efficiency estimate,
not a guarantee of future profit.
"""

from __future__ import annotations

from typing import List, Optional


# Weight of live evidence grows with sample; caps so the historical prior
# (larger n) is never fully abandoned.
_MAX_LIVE_WEIGHT = 0.6
_LIVE_SAMPLE_FOR_FULL_WEIGHT = 30


def _normalize(x: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def _simpsons_rule(a: float, b: float) -> float:
    """A simple fair combining weight between 0 and 1."""
    return a + (b - a) * 0.5


def efficiency_score(
    band_track: Optional[dict] = None,
    live_trades: Optional[List[float]] = None,
    *,
    min_meaningful_wr: float = 0.40,
) -> dict:
    """Compute the evidence-weighted efficiency summary.

    `band_track`      : dict with keys n, win_rate, mean_return, risk
    `live_trades`     : iterable of realized net returns (fraction) from closed
                        live paper trades (may be empty).

    Returns a dict: score, win_rate, expectancy, edge_ratio, live_sample,
    confidence, n (effective sample), and the raw inputs used.
    """
    bt = band_track or {}
    live = [float(r) for r in (live_trades or []) if r is not None]

    bt_n = int(bt.get("n") or 0)
    bt_wr = bt.get("win_rate")
    bt_mean = bt.get("mean_return")
    bt_risk = bt.get("risk")

    live_n = len(live)
    if live_n:
        live_wr = sum(1 for r in live if r > 0) / live_n
        live_mean = sum(live) / live_n
        live_losses = [abs(r) for r in live if r <= 0]
        live_risk = (sum(live_losses) / len(live_losses)) if live_losses else None
    else:
        live_wr = live_mean = live_risk = None

    # ---- base edge from the historical band prior ----
    edge_base = None
    if bt_mean is not None and bt_risk:
        exp_edge = bt_mean / abs(bt_risk)
        edge_base = (
            0.5 * _normalize(bt_wr or 0.0, min_meaningful_wr, 0.75)
            + 0.5 * _normalize(exp_edge, 0.0, 2.0)
            if bt_wr is not None
            else 0.5 * _normalize(exp_edge, 0.0, 2.0)
        )
    elif bt_wr is not None:
        edge_base = _normalize(bt_wr, min_meaningful_wr, 0.75)
    else:
        edge_base = 0.0

    # ---- live confirmation (adaptive) ----
    live_score = 0.0
    if live_n and live_mean is not None:
        exp_live = live_mean / abs(live_risk) if live_risk else 0.0
        live_score = (
            0.5 * _normalize(live_wr or 0.0, min_meaningful_wr, 0.75)
            + 0.5 * _normalize(exp_live, 0.0, 2.0)
        )

    # Blend: more live trades -> more weight on the live score.
    live_w = _MAX_LIVE_WEIGHT * min(1.0, live_n / _LIVE_SAMPLE_FOR_FULL_WEIGHT)
    combined = (1.0 - live_w) * edge_base + live_w * live_score

    score = round(combined * 100)

    effective_n = bt_n + live_n
    if live_n >= 30:
        confidence = "HIGH"
    elif live_n >= 10 or bt_n >= 30:
        confidence = "MEDIUM"
    elif effective_n >= 5:
        confidence = "LOW"
    else:
        confidence = "VERY LOW"

    # Expectancy / edge for display.
    if live_mean is not None and live_n:
        expectancy = live_mean
        edge_ratio = (live_mean / abs(live_risk)) if live_risk else None
    elif bt_mean is not None:
        expectancy = bt_mean
        edge_ratio = (bt_mean / abs(bt_risk)) if bt_risk else None
    else:
        expectancy = None
        edge_ratio = None

    # Effective win-rate for display (live-when-available else band).
    eff_wr = live_wr if (live_n and live_wr is not None) else bt_wr

    skill_band = "cold"
    if score >= 70:
        skill_band = "hot"
    elif score >= 55:
        skill_band = "warm"

    return {
        "score": int(score),
        "win_rate": eff_wr,
        "expectancy": expectancy,
        "edge_ratio": edge_ratio,
        "live_sample": live_n,
        "confidence": confidence,
        "n": effective_n,
        "skill_band": skill_band,
        "score_raw": combined,
    }


def efficiency_for_market(symbol: str, timeframe: str, score: int,
                          settings=None) -> dict:
    """Compute the efficiency summary for one market + setup clarity score.

    Pulls the band's realized forward track record (from the candle store)
    and the accumulating live paper trade results, then blends them.
    Best-effort: never raises - returns zeros if anything is unavailable.
    """
    try:
        from .live_runner import _projection_rows, _band_for, _live_trade_returns
    except Exception:  # noqa: BLE001
        _projection_rows = _live_trade_returns = None
        _band_for = lambda s: "SCANNED"

    try:
        proj_rows = _projection_rows(symbol, timeframe) if _projection_rows else {}
        live_trades = _live_trade_returns(symbol, timeframe) if _live_trade_returns else []
        band = _band_for(int(score or 0))
        proj = proj_rows.get(band)
        # If the exact prestige band has no realized track record yet, fall
        # back to the strongest available band so we never under-report a
        # market with a live setup.
        if proj is None and proj_rows:
            proj = max(proj_rows.values(),
                       key=lambda p: (p.get("n") or 0, p.get("mean_return") or 0))
        result = efficiency_score(proj, live_trades)
        result["band"] = band if proj is not None else (next(iter(proj_rows), "SCANNED") if proj_rows else band)
        return result
    except Exception:  # noqa: BLE001
        return {
            "score": 0, "win_rate": None, "expectancy": None, "edge_ratio": None,
            "live_sample": 0, "confidence": "VERY LOW", "n": 0, "skill_band": "cold",
            "band": "SCANNED",
        }
