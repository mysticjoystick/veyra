"""Phase 4 outcome-classification tests.

A setup that never triggers must NOT be recorded as a losing trade. This is the
core guard against inflating the trade count / win-rate with phantom outcomes.
"""

from __future__ import annotations

import pytest

from veyra.backtest.outcomes import classify_outcome
from veyra.backtest.models import SetupOutcome


def test_setup_that_traded_is_completed():
    assert classify_outcome("COMPLETED", trade_id="P1") == SetupOutcome.COMPLETED
    # Even if the setup state later drifted to the terminal EXPIRED, a setup that
    # produced a triggered result is a trade, so it is COMPLETED.
    assert classify_outcome("EXPIRED", trade_id="P1") == SetupOutcome.COMPLETED


def test_setup_that_never_traded_and_expired_is_not_a_trade():
    assert classify_outcome("EXPIRED", trade_id=None) == SetupOutcome.EXPIRED


def test_setup_invalidated_before_trading():
    assert classify_outcome("INVALIDATED", trade_id=None) == SetupOutcome.INVALIDATED


def test_qualified_but_never_triggered():
    assert classify_outcome("QUALIFIED", trade_id=None) == SetupOutcome.QUALIFIED_NO_TRADE


def test_detected_or_developing_only():
    assert classify_outcome("DETECTED", trade_id=None) == SetupOutcome.DETECTED_ONLY
    assert classify_outcome("DEVELOPING", trade_id=None) == SetupOutcome.DETECTED_ONLY