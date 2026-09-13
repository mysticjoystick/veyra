"""Tests for the live cost drift tracker."""

import time

import pytest

from veyra.alerts.costs import CostModel
from veyra.costs.live_cost_tracker import CostRecord, LiveCostTracker


class _Pos:
    def __init__(self, notional=1000.0, fees=3.0, entry=100.0, exit_ts=0):
        self.notional = notional
        self.fees = fees
        self.entry_price = entry
        self.exit_ts = exit_ts or int(time.time())


def test_record_appends_jsonl_and_realized_matches_assumption(tmp_path):
    tracker = LiveCostTracker(tmp_path / "costs.jsonl")
    rec = tracker.record("BTC/USDT", "4H", _Pos(notional=1000.0, fees=3.0))
    assert rec.realized_cost == pytest.approx(0.003)
    assert rec.assumed_round_trip == pytest.approx(CostModel().round_trip)
    assert rec.fill_slippage == pytest.approx(0.0)
    lines = (tmp_path / "costs.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert "assumed_round_trip" in lines[0]


def test_report_aggregates_zero_drift_by_default(tmp_path):
    tracker = LiveCostTracker(tmp_path / "costs.jsonl")
    for i in range(3):
        tracker.record("BTC/USDT", "4H", _Pos(notional=1000.0, fees=3.0))
    report = tracker.report()
    assert report["n_trades"] == 3
    assert report["avg_realized_cost"] == pytest.approx(0.003)
    assert report["alert"] is None
    assert len(report["weeks"]) == 1


def test_report_empty(tmp_path):
    report = LiveCostTracker(tmp_path / "costs.jsonl").report()
    assert report["n_trades"] == 0
    assert report["avg_realized_cost"] is None
    assert report["alert"] is None


def test_drift_alert_after_two_consecutive_bad_weeks(tmp_path):
    tracker = LiveCostTracker(tmp_path / "costs.jsonl", alert_pct=0.20, alert_weeks=2)
    now = int(time.time())
    week_ago = now - 7 * 86400

    def rec(ts):
        return CostRecord(
            symbol="BTC/USDT", timeframe="4H", closed_at=ts, notional=1000.0,
            assumed_round_trip=0.003, realized_cost=0.0042,  # 40% over assumed
            expected_entry=100.0, entry_price=100.0, fill_slippage=0.0,
        )

    one_bad_week = [rec(week_ago)]
    assert tracker.check_drift(rows=one_bad_week) is None  # needs 2 weeks

    two_bad_weeks = [rec(week_ago), rec(now)]
    alert = tracker.check_drift(rows=two_bad_weeks)
    assert alert is not None
    assert alert["level"] == "warning"
    assert alert["consecutive_weeks"] == 2


def test_good_week_resets_consecutive_streak(tmp_path):
    tracker = LiveCostTracker(tmp_path / "costs.jsonl", alert_pct=0.20, alert_weeks=2)
    now = int(time.time())
    week_ago = now - 7 * 86400
    two_weeks_ago = now - 14 * 86400

    def rec(ts, cost):
        return CostRecord(
            symbol="BTC/USDT", timeframe="4H", closed_at=ts, notional=1000.0,
            assumed_round_trip=0.003, realized_cost=cost,
            expected_entry=100.0, entry_price=100.0, fill_slippage=0.0,
        )

    rows = [rec(two_weeks_ago, 0.0042), rec(week_ago, 0.0030)]
    assert tracker.check_drift(rows=rows) is None