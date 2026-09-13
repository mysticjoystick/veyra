"""Tests for the candle validator."""

from __future__ import annotations

import pandas as pd
import pytest

from veyra.data.validator import Validator, ValidationReport


def _frame(**overrides) -> pd.DataFrame:
    data = {
        "open_time": [1000, 10400, 20400],
        "open": [10.0, 11.0, 12.0],
        "high": [12.0, 13.0, 14.0],
        "low": [9.0, 10.0, 11.0],
        "close": [11.0, 12.0, 13.0],
        "volume": [100.0, 110.0, 120.0],
    }
    data.update(overrides)
    return pd.DataFrame(data)


def test_valid_frame_passes():
    report = Validator(expected_interval_seconds=14400).verify(_frame())
    assert isinstance(report, ValidationReport)
    assert report.valid
    assert not report.has_gaps()
    assert report.n_invalid_rows == 0


def test_missing_column_fails():
    df = _frame().drop(columns=["volume"])
    report = Validator().verify(df)
    assert not report.valid
    assert any(i.type.value == "MISSING_COLUMN" for i in report.issues)


def test_invalid_ohlc_high_below_low():
    df = _frame().copy()
    df.loc[1, ["high", "low"]] = [8.0, 15.0]
    report = Validator().verify(df)
    assert not report.valid
    assert any(i.type.value == "OHLC_INVARIANT" for i in report.issues)
    assert report.n_invalid_rows >= 1


def test_invalid_ohlc_close_outside_range():
    df = _frame().copy()
    df.loc[1, "close"] = 30.0  # above high
    report = Validator().verify(df)
    assert not report.valid
    assert any(i.type.value == "OHLC_INVARIANT" for i in report.issues)


def test_negative_volume_detected():
    df = _frame().copy()
    df.loc[2, "volume"] = -5.0
    report = Validator().verify(df)
    assert not report.valid
    assert any(i.type.value == "NEGATIVE_VOLUME" for i in report.issues)


def test_duplicate_timestamps_detected():
    df = _frame().copy()
    df.loc[2, "open_time"] = df.loc[1, "open_time"]
    report = Validator().verify(df)
    assert not report.valid
    assert any(i.type.value == "DUPLICATE_TIMESTAMP" for i in report.issues)


def test_out_of_order_timestamps_detected():
    df = _frame().copy()
    df.loc[1, "open_time"] = 500  # before first
    report = Validator().verify(df)
    assert not report.valid
    assert any(i.type.value == "OUT_OF_ORDER_TIMESTAMP" for i in report.issues)


def test_gap_detection_reports_missing_intervals():
    # 1000, 15400, 44200 => spacing 14400 then 28800 (misses one at 29800).
    df = pd.DataFrame(
        {
            "open_time": [1000, 15400, 44200],
            "open": [10.0, 11.0, 12.0],
            "high": [12.0, 13.0, 14.0],
            "low": [9.0, 10.0, 11.0],
            "close": [11.0, 12.0, 13.0],
            "volume": [100.0, 110.0, 120.0],
        }
    )
    report = Validator(expected_interval_seconds=14400).verify(df)
    assert report.has_gaps()
    assert len(report.gaps) == 1
    assert report.gaps[0].expected_open_time == 29800


def test_no_gap_when_spacing_exact():
    df = pd.DataFrame(
        {"open_time": [1000, 15400, 29800], "open": [1, 2, 3], "high": [2, 3, 4],
         "low": [0.5, 1, 2], "close": [1.5, 2.5, 3.5], "volume": [1, 1, 1]}
    )
    report = Validator(expected_interval_seconds=14400).verify(df)
    assert not report.has_gaps()
    assert len(report.gaps) == 0


def test_interval_detection_disabled_when_none():
    df = _frame().copy()
    df.loc[2, "open_time"] = 999999999
    report = Validator(expected_interval_seconds=None).verify(df)
    # interval None means no gap detection runs
    assert not report.has_gaps()