"""Tests for timeframe normalization utilities."""

from __future__ import annotations

import pytest

from veyra.data.timeframe import (
    normalize_timeframe,
    timeframe_interval_seconds,
)
from veyra.domain import Timeframe


def test_normalize_timeframe_aliases():
    assert normalize_timeframe("4h") == Timeframe.FOUR_HOUR.value
    assert normalize_timeframe("240m") == Timeframe.FOUR_HOUR.value
    assert normalize_timeframe("1d") == Timeframe.ONE_DAY.value
    assert normalize_timeframe("86400s") == Timeframe.ONE_DAY.value
    assert normalize_timeframe("15m") == Timeframe.FIFTEEN_MIN.value
    assert normalize_timeframe("900s") == Timeframe.FIFTEEN_MIN.value
    assert normalize_timeframe("3m") == Timeframe.THREE_MIN.value
    assert normalize_timeframe("5m") == Timeframe.FIVE_MIN.value


def test_normalize_timeframe_rejects_unknown():
    with pytest.raises(ValueError):
        normalize_timeframe("7m")


def test_interval_seconds_defaults():
    assert timeframe_interval_seconds("4H") == 14400
    assert timeframe_interval_seconds("1D") == 86400
    assert timeframe_interval_seconds("15m") == 900
    assert timeframe_interval_seconds("3m") == 180
    assert timeframe_interval_seconds("5m") == 300


def test_interval_seconds_uses_supplied_map():
    custom = {"4H": 14400, "1D": 86400}
    assert timeframe_interval_seconds("4H", custom) == 14400


def test_interval_seconds_rejects_undefine():
    with pytest.raises(ValueError):
        timeframe_interval_seconds("7m")