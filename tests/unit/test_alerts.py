"""Tests for the alert layer (selectivity, determinism, serialization)."""

from __future__ import annotations

from veyra.alerts import AlertLevel, AlertPolicy, AlertProcessor, SetupAlert
from veyra.backtest import SimulationResult
from veyra.backtest.models import SetupRecord


def _rec(
    key,
    score,
    state="QUALIFIED",
    outcome="QUALIFIED_NO_TRADE",
    det_ts=1_000_000_000,
    setup_type="TREND_CONTINUATION",
    side="LONG",
    regime="BULL",
):
    return SetupRecord(
        key=key,
        symbol="BTC/USDT",
        timeframe="4H",
        detection_ts=det_ts,
        setup_type=setup_type,
        regime=regime,
        score=score,
        score_normalized=score,
        side=side,
        interest_area_low=50_000.0,
        interest_area_high=51_000.0,
        invalidation="close_below 49_000",
        final_state=state,
        outcome=outcome,
        trade_id=None,
    )


def _result(setups):
    return SimulationResult(run=None, setups=list(setups))


def test_policy_filters_by_min_score():
    p = AlertPolicy(min_score=55)
    assert p.allows("QUALIFIED", 80) is True
    assert p.allows("QUALIFIED", 55) is True
    assert p.allows("QUALIFIED", 40) is False


def test_policy_never_alerts_detection_only():
    p = AlertPolicy(min_score=0)
    assert p.allows("DETECTED", 90) is False
    assert p.allows("DETECTED_ONLY", 90) is False


def test_processor_only_emits_qualifying_and_scored():
    proc = AlertProcessor(AlertPolicy(min_score=55))
    alerts = proc.run(
        _result(
            [
                _rec("a", 90, outcome="QUALIFIED_NO_TRADE"),
                _rec("b", 40, outcome="QUALIFIED_NO_TRADE"),  # below threshold
                _rec("c", 80, outcome="DETECTED_ONLY"),  # never qualified
            ]
        )
    )
    keys = [a.setup_key for a in alerts]
    assert keys == ["a"]


def test_processor_sorts_high_score_first_within_same_bar():
    proc = AlertProcessor(AlertPolicy(min_score=0))
    alerts = proc.run(
        _result(
            [
                _rec("low", 60, det_ts=100),
                _rec("high", 95, det_ts=100),
            ]
        )
    )
    assert [a.setup_key for a in alerts] == ["high", "low"]


def test_processor_enforces_max_per_window():
    # 3 alerts within 100s, cap 2 per 1000s window -> only the first 2 kept.
    proc = AlertProcessor(
        AlertPolicy(min_score=0, max_per_window=2, window_seconds=1000)
    )
    alerts = proc.run(
        _result(
            [
                _rec("w1", 90, det_ts=100),
                _rec("w2", 88, det_ts=150),
                _rec("w3", 85, det_ts=200),
            ]
        )
    )
    return_alerts = [a.setup_key for a in alerts]
    assert len(alerts) == 2
    assert return_alerts == ["w1", "w2"]


def test_processor_allows_outside_window():
    # Two alerts far apart in time exceed no cap.
    proc = AlertProcessor(
        AlertPolicy(min_score=0, max_per_window=2, window_seconds=1000)
    )
    alerts = proc.run(
        _result(
            [
                _rec("a", 90, det_ts=100),
                _rec("b", 88, det_ts=1_000_000),  # far outside window
            ]
        )
    )
    assert [a.setup_key for a in alerts] == ["a", "b"]


def test_processor_is_deterministic():
    proc = AlertProcessor(AlertPolicy(min_score=55))
    setups = [
        _rec("a", 90, det_ts=100),
        _rec("b", 70, det_ts=200),
        _rec("c", 40, det_ts=300),
    ]
    a1 = [x.setup_key for x in proc.run(_result(setups))]
    a2 = [x.setup_key for x in proc.run(_result(setups))]
    assert a1 == a2 == ["a", "b"]


def test_level_bands_are_score_derived():
    assert AlertLevel.for_score(85).name == "CONVERGENT"
    assert AlertLevel.for_score(60).name == "DIRECTIONAL"
    assert AlertLevel.for_score(45).name == "EMERGENT"
    assert AlertLevel.for_score(10).name == "SCANNED"

    # Every band carries an honest clarity tagline, never a profit promise.
    for band in AlertLevel.bands():
        assert band.tagline
        assert "profit" not in band.tagline.lower()


def test_alert_serializes_and_round_trips():
    alert = SetupAlert(
        alert_id="4H|a",
        symbol="BTC/USDT",
        timeframe="4H",
        setup_key="a",
        setup_type="TREND_CONTINUATION",
        side="LONG",
        regime="BULL",
        timestamp=1_000_000_000,
        overall_score=90,
        score_normalized=90,
        level="CONVERGENT",
        interest_area_low=50_000.0,
        interest_area_high=51_000.0,
        invalidation="close_below 49_000",
        reasoning="TREND_CONTINUATION LONG BULL score=90",
        properties={"source": "paper"},
    )
    restored = SetupAlert.from_dict(alert.to_dict())
    assert restored == alert