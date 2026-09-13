"""Tests for the offline weight-search / re-validation math."""

import pytest

from veyra.research.weight_search import (
    bootstrap_ci,
    split_by_ts,
    evaluate,
    _posterior_gate,
    row_score,
    band_for,
    _weight_combos,
)

COMPONENTS = ["TREND", "STRUCTURE", "PULLBACK", "MOMENTUM", "VOLUME", "VOLATILITY"]
BASELINE = {"TREND": 0.25, "STRUCTURE": 0.20, "PULLBACK": 0.15,
            "MOMENTUM": 0.12, "VOLUME": 0.07, "VOLATILITY": 0.05}


def _row(ts, score, net, base=70):
    return {
        "ts": ts, "symbol": "BTC/USDT", "timeframe": "4H",
        "setup_type": "TREND_CONTINUATION", "side": "LONG", "regime": "BULL",
        "scores": {c: _component(score, base) for c in COMPONENTS},
        "net_24h": net,
    }


def _component(score, base):
    return base if score == base else (95 if score > base else 40)


def test_row_score_is_weighted_average():
    row = _row(1, score=95, net=0.01, base=70)
    manual = sum(BASELINE[c] * row["scores"][c] for c in COMPONENTS) / sum(BASELINE.values())
    assert row_score(row, BASELINE) == round(manual)


def test_band_for_ladder():
    assert band_for(80) == "CONVERGENT"
    assert band_for(60) == "DIRECTIONAL"
    assert band_for(40) == "EMERGENT"
    assert band_for(10) == "SCANNED"


def test_split_is_chronological():
    rows = [_row(ts, 70, 0.0) for ts in range(100)]
    train, val, test = split_by_ts(rows)
    assert len(train) + len(val) + len(test) == 100
    assert max(r["ts"] for r in train) <= min(r["ts"] for r in val)
    assert max(r["ts"] for r in val) <= min(r["ts"] for r in test)


def test_weight_combos_are_normalized_to_baseline_sum():
    combos = _weight_combos(BASELINE)
    assert len(combos) >= 10
    base_sum = sum(BASELINE.values())
    for combo in combos:
        assert sum(combo.values()) == pytest.approx(base_sum)
    assert BASELINE in combos


def test_evaluate_counts_only_eligible_and_measures_expectancy():
    # Fiscal design: high-score rows make money, low-score rows lose.
    rows = ([_row(ts, 95, 0.01) for ts in range(10)] +
            [_row(ts, 50, -0.02) for ts in range(10)])
    train = rows[:10]
    gate = _posterior_gate(train, BASELINE)
    out = evaluate(rows, BASELINE, gate)
    assert out["n_eligible"] == 10
    assert out["expectancy"] == pytest.approx(0.01)


def test_bootstrap_ci_contains_sample_mean():
    rets = [0.005, -0.001, 0.012, 0.003, 0.002, 0.006] * 10
    mean = sum(rets) / len(rets)
    ci = bootstrap_ci(rets, seeds=100)
    assert ci["lo"] <= mean <= ci["hi"]


def test_posterior_gate_skips_scanned_and_has_risk():
    rows = ([_row(ts, 95, 0.01) for ts in range(6)] +
            [_row(ts, 95, -0.02) for ts in range(2)] +
            [_row(ts, 10, -0.04) for ts in range(2)])
    gate = _posterior_gate(rows, BASELINE)
    assert "CONVERGENT" in gate
    assert "SCANNED" not in gate
    assert gate["CONVERGENT"]["n"] == 8
    assert gate["CONVERGENT"]["wins"] == 6
    assert gate["CONVERGENT"]["risk"] == pytest.approx(-0.02)