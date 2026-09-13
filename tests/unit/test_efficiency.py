"""Tests for the live efficiency score (evidence-weighted, 0-100)."""

from __future__ import annotations

from veyra.efficiency import efficiency_score


def test_empty_everything_is_zero_low():
    r = efficiency_score(None, None)
    assert r["score"] == 0
    assert r["live_sample"] == 0
    assert r["confidence"] == "VERY LOW"


def test_clean_band_prior_gives_high_score():
    band = {"n": 300, "win_rate": 0.70, "mean_return": 0.03, "risk": 0.02}
    r = efficiency_score(band, [])
    assert r["win_rate"] == 0.70
    assert r["live_sample"] == 0
    assert r["confidence"] == "MEDIUM"
    assert 60 <= r["score"] <= 90


def test_live_wins_lift_score_over_flat_prior():
    band = {"n": 300, "win_rate": 0.50, "mean_return": 0.0, "risk": 0.02}
    # 25 straight winners -> strong adaptive lift, larger live sample.
    live = [0.01] * 25
    flat = efficiency_score(band, [])
    hot = efficiency_score(band, live)
    assert hot["score"] > flat["score"]
    assert hot["win_rate"] == 1.0
    assert hot["live_sample"] == 25


def test_live_losses_pull_score_down():
    band = {"n": 300, "win_rate": 0.70, "mean_return": 0.03, "risk": 0.02}
    losers = [-0.02] * 25
    base = efficiency_score(band, [])
    bad = efficiency_score(band, losers)
    # Losses must reduce expected value -> score drops.
    assert bad["score"] < base["score"]


def test_full_live_sample_reaches_high_confidence():
    band = {"n": 300, "win_rate": 0.60, "mean_return": 0.01, "risk": 0.02}
    r = efficiency_score(band, [0.005] * 30)
    assert r["confidence"] == "HIGH"
    assert r["live_sample"] == 30
