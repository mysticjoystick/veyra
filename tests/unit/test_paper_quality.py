"""Tests for the shared trade-quality gate (Bayesian posterior, no bypass)."""

import pytest

from veyra.paper.quality import (
    PRIOR_N,
    evaluate_eligibility,
    gate_rejects,
    posterior_stats,
)


def _stats(mean=0.06, wr=0.70, risk=0.02, n=52):
    return type("S", (), {"n": n, "win_rate": wr, "mean_return": mean, "risk": risk})()


def test_posterior_of_no_evidence_is_the_neutral_prior():
    pm, pw, n = posterior_stats(None)
    assert n == 0
    assert pm == pytest.approx(0.0)
    assert pw == pytest.approx(0.50)


def test_posterior_updates_with_evidence():
    pm, pw, n = posterior_stats(_stats(mean=0.05, wr=0.60, risk=0.02, n=80))
    assert n == 80
    # posterior mean = (0 + 80*0.05) / (20 + 80) = 0.04
    assert pm == pytest.approx(0.04)
    # posterior win rate = (0.50*20 + 48) / 100 = 0.58
    assert pw == pytest.approx(0.58)


def test_gate_rejects_zero_evidence():
    # prior win-rate 0.50 < floor 0.55 -> always rejects when mean 0
    assert gate_rejects(None) is not None


def test_gate_rejects_when_floor_not_reached_even_with_some_wins():
    # 8 obs with 50% wins: posterior (10+4)/28 = 0.50 < floor 0.55.
    st = _stats(mean=0.10, wr=0.5, risk=0.01, n=8)
    assert gate_rejects(st) is not None
    _, pw, _ = posterior_stats(st)
    assert pw < 0.55


def test_gate_passes_with_strong_evidence():
    st = _stats(mean=0.06, wr=0.70, risk=0.02, n=52)
    assert gate_rejects(st) is None


def test_gate_rejects_when_posterior_mean_below_risk():
    st = _stats(mean=0.01, wr=1.0, risk=0.02, n=200)
    assert gate_rejects(st) is not None


def test_eligibility_flags_band_and_reason():
    # Below the EMERGENT floor: not tradeable, explicit reason.
    assert evaluate_eligibility(10) == {
        "band": "SCANNED",
        "trade_eligible": False,
        "eligibility_reason": "raw_score_below_band_floor",
    }
    # Tradeable band but no evidence view at this layer -> pending, not claimed.
    r = evaluate_eligibility(85)
    assert r["band"] == "CONVERGENT"
    assert r["trade_eligible"] is False
    assert r["eligibility_reason"] == "quality_gate_pending"
    # Thin real evidence that fails the gate -> cold-start reason.
    r = evaluate_eligibility(85, _stats(mean=0.05, wr=1.0, risk=0.01, n=4))
    assert r["trade_eligible"] is False
    assert r["eligibility_reason"] == "cold_start_insufficient_history"
    # Strong evidence -> eligible.
    r = evaluate_eligibility(85, _stats(mean=0.06, wr=0.70, risk=0.02, n=52))
    assert r["trade_eligible"] is True
    assert r["eligibility_reason"] == "eligible"
    # Real evidence that fails the gate -> quality_gate_failed.
    r = evaluate_eligibility(85, _stats(mean=0.06, wr=0.50, risk=0.02, n=200))
    assert r["trade_eligible"] is False
    assert r["eligibility_reason"] == "quality_gate_failed"