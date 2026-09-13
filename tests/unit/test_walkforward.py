"""Tests for the Step-1 walk-forward / out-of-sample gate."""

from __future__ import annotations

from veyra.alerts.walkforward import (
    GATED_BANDS,
    run_walkforward,
    _split_alerts,
    _stats,
)


def _alerts(n, band="CONVERGENT", win=True, mean=0.03):
    """n chronologically-ordered alerts with realized_24h near `mean`."""
    out = []
    for i in range(n):
        r = mean if win or i % 2 == 0 else -mean
        out.append({"band": band, "realized_24h": r})
    return out


def test_split_holds_out_youngest_fraction_chronologically():
    alerts = [{"band": "X", "realized_24h": float(i)} for i in range(100)]
    train, test = _split_alerts(alerts, 0.30)
    assert len(train) == 70
    assert len(test) == 30
    assert test[0]["realized_24h"] == 70.0
    assert test[-1]["realized_24h"] == 99.0


def test_stats_compute_win_rate_mean_risk():
    s = _stats([0.10, 0.05, -0.04, -0.02])
    assert s.n == 4
    assert abs(s.win_rate - 0.5) < 1e-9
    assert abs(s.mean_return - 0.0225) < 1e-9
    assert abs(s.risk - 0.03) < 1e-9


def test_pass_when_band_holds_its_edge_out_of_sample():
    # train strong, test still positive with similar win rate
    train, test = _alerts(50, win=True, mean=0.04), _alerts(20, win=True, mean=0.03)
    result = run_walkforward(
        [{"symbol": "BTC/USDT", "timeframe": "1D", "alerts": train + test}], fraction=0.30
    )
    assert result.markets[0].gate == "PASS"
    assert result.markets[0].bands[0].gate == "PASS"
    assert result.gate == "PASS"


def test_fail_when_band_collapses_out_of_sample():
    # train positive, test loses money -> edge did not survive
    train = _alerts(50, win=True, mean=0.04)
    test = [{"band": "CONVERGENT", "realized_24h": -0.03} for _ in range(20)]
    result = run_walkforward(
        [{"symbol": "BTC/USDT", "timeframe": "1D", "alerts": train + test}], fraction=0.30
    )
    assert result.markets[0].bands[0].gate == "FAIL"
    assert result.markets[0].gate == "FAIL"
    assert result.gate == "FAIL"


def test_small_test_sample_passes_instead_of_disqualifying():
    train = _alerts(6, win=True, mean=0.03)
    test = _alerts(2, win=True, mean=0.05)  # only 2 unseen samples in the 30% slice
    result = run_walkforward(
        [{"symbol": "BTC/USDT", "timeframe": "4H", "alerts": train + test}], fraction=0.30
    )
    assert result.markets[0].n_test == 2
    band = result.markets[0].bands[0]
    assert band.gate == "PASS"
    assert "insufficient evidence" in band.note.lower()


def test_gated_bands_only_tracks_alignment_ladder():
    assert GATED_BANDS == ["CONVERGENT", "DIRECTIONAL", "EMERGENT"]


def test_min_test_samples_is_enforced():
    from veyra.alerts.walkforward import MIN_TEST_SAMPLES
    assert MIN_TEST_SAMPLES == 3


def test_empty_input_informs_insufficient():
    result = run_walkforward([])
    assert result.gate == "INSUFFICIENT"
    assert result.gate_reason == "no market data"