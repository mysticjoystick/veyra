"""Quantified market decision gate — "is this setup worth considering?".

This is the single source of truth that turns a detected setup + its
historical evidence into a TRADE / WATCH / STAND_ASIDE verdict.

Why this exists
--------------
The live feed used to surface every detected setup with equal weight —
including setups whose own track record says they lose money
(e.g. SOL 1H LONG: 0% win-rate, -0.49% expectancy, efficiency 0/100).
That is honest but not useful: a trader cannot tell what is worth
considering.

This gate fixes that without hiding anything:

* TRADE       — every strict check passes. Worth considering right now,
                with trigger / invalidation / size.
* WATCH       — positive expectancy but a flaw (thin R:R, unconfirmed
                efficiency, live contradiction). Monitor, do not size.
* STAND_ASIDE — no edge on current evidence. Shown for transparency,
                never alerted, never paper-traded.

TRADE requires ALL of:

  1. clarity score >= 55 (DIRECTIONAL floor; SCANNED/EMERGENT never trade)
  2. band sample n >= 20 (PRIOR_N — no cold-start trades)
  3. Bayesian posterior gate passes (win >= 55%, mean > 0, mean > risk)
  4. raw win-rate >= 50% and raw expectancy > 0 (no negative-edge bands)
  5. historical R:R = |mean| / |risk| >= 1.0 (upside must outweigh downside)
  6. evidence-weighted efficiency >= 55 (confirmed, not just aligned)
  7. no live contradiction (5+ live trades averaging <= 0 downgrades)

Anything failing 1-4 is STAND_ASIDE. Failing 5-7 is WATCH when the
posterior gate itself passes (edge exists but is flawed), otherwise
STAND_ASIDE. Every verdict carries machine-readable reasons plus the
display numbers (potential / risk / R:R / sample / confidence) so the
dashboard and Telegram can render the exact evidence layer.

Simulation only: this never places an order. It is a reporting gate.
"""

from __future__ import annotations

from typing import Dict, List, Optional

# Strict floors — documented, conservative, shared by dashboard / alerts / paper.
MIN_TRADE_SCORE = 55          # DIRECTIONAL floor
MIN_TRADE_SAMPLE = 20         # PRIOR_N — no cold-start trades
MIN_POSTERIOR_WIN_RATE = 0.55
MIN_RAW_WIN_RATE = 0.50
MIN_RR = 1.0                  # upside must outweigh downside
MIN_EFFICIENCY_TRADE = 55     # evidence-weighted confirmation
MIN_EFFICIENCY_WATCH = 40     # below this there is no edge at all
LIVE_CONTRADICTION_N = 5      # live sample size that can downgrade a TRADE


def _as_dict(stats) -> dict:
    """Coerce a ForwardStats / SimpleNamespace / dict to a plain dict."""
    if stats is None:
        return {}
    if isinstance(stats, dict):
        return stats
    return {
        "n": getattr(stats, "n", 0),
        "win_rate": getattr(stats, "win_rate", None),
        "mean_return": getattr(stats, "mean_return", None),
        "risk": getattr(stats, "risk", None),
    }


def _parse_float(value) -> Optional[float]:
    try:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return float(str(value).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None


def _structural_stop_pct(setup: Optional[dict]) -> Optional[float]:
    """Structural risk distance for display: max(invalidation, ATR*2) / entry."""
    if not setup:
        return None
    ia = setup.get("interest_area") or {}
    low = _parse_float(ia.get("low")) if isinstance(ia, dict) else None
    high = _parse_float(ia.get("high")) if isinstance(ia, dict) else None
    entry = (low + high) / 2.0 if (low and high and high > 0) else None
    if not entry or entry <= 0:
        return None
    side = str(setup.get("side") or "LONG").upper()
    bias = 1.0 if side in ("LONG", "BUY") else -1.0
    inval = _parse_float(setup.get("invalidation"))
    structural = 0.0
    if inval and inval > 0:
        if (bias > 0 and inval < entry) or (bias < 0 and inval > entry):
            structural = abs(entry - inval) / entry
    atr = _parse_float(setup.get("atr"))
    atr_dist = (atr * 2.0) / entry if (atr and atr > 0) else 0.0
    stop_pct = max(structural, atr_dist)
    if stop_pct <= 0:
        stop_pct = 0.02  # fallback 2% mirrors the paper engine
    return stop_pct


def decide(
    score: int,
    band_stats=None,
    efficiency: Optional[dict] = None,
    setup: Optional[dict] = None,
    live_returns: Optional[list] = None,
) -> dict:
    """Return the quantified verdict for one live setup.

    Args:
        score: canonical 0-100 clarity score.
        band_stats: per-band realized track record (n, win_rate, mean_return,
            risk) — net of costs. Dict, ForwardStats, or SimpleNamespace.
        efficiency: evidence-weighted summary from efficiency_score()
            (score, win_rate, expectancy, confidence, live_sample).
        setup: live setup dict (side, interest_area, invalidation, atr) used
            only to derive the structural stop distance for display R:R.
        live_returns: raw closed-trade net returns for contradiction check
            (optional; efficiency['live_sample'] is used when omitted).

    Returns a dict with verdict, reasons, and every display number.
    Never raises on missing data — missing evidence is STAND_ASIDE.
    """
    from .alerts.models import AlertLevel
    from .paper.quality import gate_rejects

    score = int(score or 0)
    band = AlertLevel.for_score(score).name
    st = _as_dict(band_stats)
    eff = efficiency or {}

    n = int(st.get("n") or 0)
    wr = st.get("win_rate")
    mean = st.get("mean_return")
    risk = st.get("risk")

    eff_score = eff.get("score")
    eff_conf = eff.get("confidence", "VERY LOW")
    live_n = int(eff.get("live_sample") or 0)
    if live_returns is not None:
        try:
            live_list = [float(r) for r in live_returns if r is not None]
        except (TypeError, ValueError):
            live_list = []
        live_n = len(live_list)
        live_mean = (sum(live_list) / len(live_list)) if live_list else None
        live_wr = (sum(1 for r in live_list if r > 0) / len(live_list)) if live_list else None
    else:
        live_mean = None
        live_wr = None
        # efficiency already blends live; recover contradiction from its inputs
        # when raw returns are unavailable: negative blended expectancy with a
        # real live sample means live is dragging the prior down.
        if live_n >= LIVE_CONTRADICTION_N and eff.get("expectancy") is not None:
            try:
                if float(eff["expectancy"]) <= 0:
                    live_mean = float(eff["expectancy"])
            except (TypeError, ValueError):
                pass

    # Risk: historical losing-move size is the gate (what the band actually
    # lost on average); the structural stop distance is display-only context
    # for sizing. Gating on max(hist, structural) would block every wide-stop
    # setup even when its historical R:R is strong — the posterior gate
    # already compares mean vs historical risk.
    stop_pct = _structural_stop_pct(setup)
    hist_risk = abs(float(risk)) if risk is not None else None
    risk_pct: Optional[float] = None
    if hist_risk is not None and stop_pct is not None:
        risk_pct = max(hist_risk, stop_pct)
    elif hist_risk is not None:
        risk_pct = hist_risk
    elif stop_pct is not None:
        risk_pct = stop_pct

    potential = float(mean) if mean is not None else None
    # Gate R:R uses historical risk only (the band's realized downside).
    rr_hist: Optional[float] = None
    if potential is not None and hist_risk and hist_risk > 1e-9:
        rr_hist = abs(potential) / abs(hist_risk)
    # Display R:R uses the conservative downside (max of hist + structural).
    rr: Optional[float] = None
    if potential is not None and risk_pct and risk_pct > 1e-9:
        rr = abs(potential) / abs(risk_pct)

    reasons: List[str] = []
    verdict = "TRADE"

    # 1. Clarity floor.
    if score < MIN_TRADE_SCORE:
        reasons.append(f"raw_score_below_band_floor: score {score} < {MIN_TRADE_SCORE} ({band})")
        verdict = "STAND_ASIDE"

    # 2. Sample floor — no cold-start trades.
    if verdict == "TRADE" and n < MIN_TRADE_SAMPLE:
        reasons.append(f"cold_start_insufficient_history: n={n} < {MIN_TRADE_SAMPLE}")
        verdict = "STAND_ASIDE"

    # 3. Bayesian posterior gate — split into hard vs soft fails.
    #    Hard fail (no edge): posterior win < 55% or posterior mean <= 0
    #      -> STAND_ASIDE, never traded, never alerted.
    #    Soft fail (edge exists, payoff poor): posterior mean <= historical
    #    risk -> WATCH (monitor, do not size). This is the R:R<1 case the
    #    user flagged: e.g. +1.87% vs -2.04% is real expectancy with bad
    #    payoff — worth seeing transparently, not worth sizing.
    if verdict == "TRADE":
        from .paper.quality import PRIOR_MEAN_RETURN, PRIOR_N, PRIOR_WIN_RATE

        _den = PRIOR_N + n
        _pm = (PRIOR_MEAN_RETURN * PRIOR_N + (float(mean) * n if mean is not None else 0)) / _den if _den else 0.0
        _pw = (PRIOR_WIN_RATE * PRIOR_N + ((float(wr) * n) if wr is not None else 0)) / _den if _den else 0.5
        if _pm <= 0 or _pw < MIN_POSTERIOR_WIN_RATE:
            reasons.append(
                f"quality_gate_failed: posterior win {_pw*100:.1f}% / mean {_pm*100:+.2f}% (n={n})"
            )
            verdict = "STAND_ASIDE"
        elif hist_risk is not None and _pm <= abs(float(hist_risk)):
            reasons.append(
                f"payoff_poor_vs_downside: posterior mean {_pm*100:+.2f}% <= "
                f"avg loss {abs(float(hist_risk))*100:.2f}% (n={n})"
            )
            verdict = "WATCH"

    # 4. Raw edge must be positive (catches 0%-win / negative-expectancy bands
    #    explicitly so the reason names the actual numbers).
    if verdict == "TRADE":
        if wr is not None and wr < MIN_RAW_WIN_RATE:
            reasons.append(f"negative_edge_win_rate: {wr*100:.0f}% < 50%")
            verdict = "STAND_ASIDE"
        elif mean is not None and mean <= 0:
            reasons.append(f"negative_expectancy: {mean*100:+.2f}%/trade <= 0")
            verdict = "STAND_ASIDE"

    # 5. R:R — upside must outweigh downside (historical). A passing
    #    posterior with poor R:R is WATCH (edge exists, payoff is bad), not
    #    TRADE. Uses historical risk, not the structural stop: the stop sizes
    #    the position, history judges the edge.
    if verdict == "TRADE" and rr_hist is not None and rr_hist < MIN_RR:
        reasons.append(
            f"poor_reward_to_risk: R:R {rr_hist:.2f} < {MIN_RR:.2f} "
            f"(+{abs(potential or 0)*100:.2f}% vs -{abs(hist_risk or 0)*100:.2f}%)"
        )
        verdict = "WATCH"

    # 6. Efficiency confirmation.
    if verdict == "TRADE" and eff_score is not None:
        try:
            es = int(eff_score)
        except (TypeError, ValueError):
            es = None
        if es is not None:
            if es < MIN_EFFICIENCY_WATCH:
                reasons.append(f"weak_efficiency: {es}/100 < {MIN_EFFICIENCY_WATCH}")
                verdict = "STAND_ASIDE"
            elif es < MIN_EFFICIENCY_TRADE:
                reasons.append(f"efficiency_not_confirmed: {es}/100 < {MIN_EFFICIENCY_TRADE}")
                verdict = "WATCH"

    # 7. Live contradiction — real closed trades disagree with history.
    if verdict == "TRADE" and live_n >= LIVE_CONTRADICTION_N and live_mean is not None:
        try:
            if float(live_mean) <= 0:
                reasons.append(
                    f"live_evidence_contradicts: {live_n} live trades avg "
                    f"{float(live_mean)*100:+.2f}%"
                )
                verdict = "WATCH"
        except (TypeError, ValueError):
            pass

    if verdict == "TRADE":
        reasons.append("eligible")

    # Trigger / invalidation strings for display (never fabricated prices).
    trigger = None
    invalidation = None
    if setup:
        ia = setup.get("interest_area") or {}
        side = str(setup.get("side") or "").upper()
        if isinstance(ia, dict) and ia.get("low") is not None and ia.get("high") is not None:
            try:
                lo_f, hi_f = float(ia["low"]), float(ia["high"])
                tf = setup.get("time") or setup.get("timeframe") or ""
                if side in ("LONG", "BUY"):
                    trigger = f"{tf} close above ${hi_f:,.1f}" if tf else f"close above ${hi_f:,.1f}"
                elif side in ("SHORT", "SELL"):
                    trigger = f"{tf} close below ${lo_f:,.1f}" if tf else f"close below ${lo_f:,.1f}"
            except (TypeError, ValueError):
                pass
        inv = setup.get("invalidation")
        if inv is not None and str(inv).strip().lower() not in ("", "n/a"):
            invalidation = str(inv)

    out: Dict[str, object] = {
        "verdict": verdict,
        "reasons": reasons,
        "score": score,
        "band": band,
        "n": n,
        "win_rate": wr,
        "expectancy": mean,
        "risk": risk_pct,
        "historical_risk": hist_risk,
        "structural_stop_pct": stop_pct,
        "potential": potential,
        "rr": rr,
        "rr_hist": rr_hist,
        "efficiency_score": eff_score,
        "efficiency_confidence": eff_conf,
        "live_sample": live_n,
        "trigger": trigger,
        "invalidation": invalidation,
    }
    return out


def verdict_for_market(
    score: int,
    proj_rows: Optional[dict] = None,
    live_trades: Optional[list] = None,
    setup: Optional[dict] = None,
    settings=None,
) -> dict:
    """Convenience wrapper that builds efficiency + decision in one call.

    `proj_rows` is the {band: {n, win_rate, mean_return, risk}} map from
    live_runner._projection_rows. Picks the band for `score`, falling back to
    the highest-n band (same fallback as efficiency_for_market).
    """
    from .alerts.models import AlertLevel
    from .efficiency import efficiency_score

    rows = proj_rows or {}
    band = AlertLevel.for_score(int(score or 0)).name
    proj = rows.get(band)
    if proj is None and rows:
        proj = max(rows.values(), key=lambda p: ((p.get("n") or 0), (p.get("mean_return") or 0)))
    eff = efficiency_score(proj, live_trades or [])
    return decide(score, proj, eff, setup=setup, live_returns=live_trades or [])
