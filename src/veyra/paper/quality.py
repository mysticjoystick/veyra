"""Shared trade-quality evaluation: Bayesian posterior gate + eligibility flag.

Single source of truth for the TRADE rule and the band->eligibility contract
used by the live scan, the dashboard and the paper engine.

Eligibility contract (one ladder, one gate):
    band             : single AlertLevel ladder on the canonical 0-100 score.
    trade_eligible   : True only when the setup could actually be traded right
                       now (band above SCANNED AND the Bayesian posterior gate
                       passes with the available band evidence).
    eligibility_reason: one of
        "raw_score_below_band_floor"        band == SCANNED
        "cold_start_insufficient_history"   gate failed and evidence < prior count
        "quality_gate_failed"               gate failed on real evidence
        "quality_gate_pending"              no evidence view available here
        "eligible"
"""

from __future__ import annotations

from typing import Optional

PRIOR_N = 20                 # pseudo-observations of neutrality
PRIOR_WIN_RATE = 0.50        # neutral prior: no better than a coin flip
PRIOR_MEAN_RETURN = 0.0      # neutral prior: no expected edge
MIN_POSTERIOR_WIN_RATE = 0.55


def posterior_stats(st) -> tuple:
    """Shrink band evidence toward a neutral prior.

    Returns (posterior_mean_return, posterior_win_rate, n_observed). A ``None``
    stats object is treated as n_observed = 0: the posterior IS the prior, which
    never passes the 0.55 floor (0.50). There is no bypass for thin evidence.
    """
    n = st.n if st is not None else 0
    wins = (st.win_rate or 0) * n if st is not None else 0
    total_return = (st.mean_return or 0) * n if st is not None else 0
    denom = PRIOR_N + n
    posterior_mean = (PRIOR_MEAN_RETURN * PRIOR_N + total_return) / denom
    posterior_win_rate = (PRIOR_WIN_RATE * PRIOR_N + wins) / denom
    return posterior_mean, posterior_win_rate, n


def gate_rejects(st) -> Optional[str]:
    """Return the blocking reason when the posterior gate rejects band evidence,
    or ``None`` when the gate passes."""
    pm, pw, _ = posterior_stats(st)
    if pm <= 0:
        return "quality_gate_failed"
    if pw < MIN_POSTERIOR_WIN_RATE:
        return "quality_gate_failed"
    risk = st.risk if st is not None else None
    if risk is not None and pm <= risk:
        return "quality_gate_failed"
    return None


def evaluate_eligibility(score: int, st=None) -> dict:
    """Single-ladder eligibility for one setup.

    With ``st is None`` (no band evidence available at this layer, e.g. the live
    scan) a non-SCANNED setup is reported ``quality_gate_pending`` rather than
    silently claimed eligible: the paper engine re-evaluates with real stats.
    """
    from ..alerts.models import AlertLevel

    level = AlertLevel.for_score(int(score or 0))
    band = level.name
    if band == "SCANNED":
        return {
            "band": band,
            "trade_eligible": False,
            "eligibility_reason": "raw_score_below_band_floor",
        }
    if st is None:
        return {
            "band": band,
            "trade_eligible": False,
            "eligibility_reason": "quality_gate_pending",
        }
    reason = gate_rejects(st)
    if reason is not None:
        _, _, n = posterior_stats(st)
        if n < PRIOR_N:
            reason = "cold_start_insufficient_history"
        return {
            "band": band,
            "trade_eligible": False,
            "eligibility_reason": reason,
        }
    return {
        "band": band,
        "trade_eligible": True,
        "eligibility_reason": "eligible",
    }