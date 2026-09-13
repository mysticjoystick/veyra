"""Phase 4 execution-semantics tests: entry/exit, ambiguous candles, fees.

These test the conservative, deterministic execution model of the backtester:
entry at the next bar's open, stop/target handling, the worst-case ambiguous
candle policy, expiry/end-of-data closes, and fee/slippage application.
"""

from __future__ import annotations

import pytest

from veyra.backtest.execution import (
    Bar,
    compute_exit,
    exit_price_adjusted,
    effective_entry_price,
)


def test_stop_only_when_no_target():
    bar = Bar(open=100, high=101, low=98, close=99)
    assert compute_exit(bar, "LONG", stop=98.5, target=None) == (98.5, "STOP")


def test_target_exactly_touched():
    bar = Bar(open=100, high=102, low=101, close=101.5)
    assert compute_exit(bar, "LONG", stop=98, target=102) == (102.0, "TARGET")


def test_ambiguous_candle_resolves_to_stop_first():
    # Bar pierces both stop (98) and target (102): intrabar order unknown.
    bar = Bar(open=100, high=103, low=97, close=100)
    assert compute_exit(bar, "LONG", stop=98, target=102) == (98.0, "STOP")


def test_ambiguous_candle_target_first_when_policy_says_so():
    bar = Bar(open=100, high=103, low=97, close=100)
    assert compute_exit(bar, "LONG", stop=98, target=102, ambiguous_candle_policy="target_first") == (
        102.0,
        "TARGET",
    )


def test_no_exit_when_bar_within_range():
    bar = Bar(open=100, high=101, low=99, close=100)
    assert compute_exit(bar, "LONG", stop=98, target=102) is None


def test_short_side_stop_and_target():
    # Only the target (below) is touched: high stays under the stop.
    bar = Bar(open=100, high=100.5, low=96, close=97)
    assert compute_exit(bar, "SHORT", stop=101, target=97) == (97.0, "TARGET")


def test_short_ambiguous_resolves_to_stop():
    bar = Bar(open=100, high=103, low=96, close=100)
    assert compute_exit(bar, "SHORT", stop=101, target=97) == (101.0, "STOP")


def test_entry_price_slippage_and_spread_against_long():
    raw = 100.0
    # LONG buys the offered side: spread/2 higher, slippage higher.
    assert effective_entry_price(raw, "LONG", 0.002, 0.0) == pytest.approx(100.2)
    assert effective_entry_price(raw, "LONG", 0.0, 0.004) == pytest.approx(100.2)


def test_entry_price_slippage_and_spread_against_short():
    raw = 100.0
    assert effective_entry_price(raw, "SHORT", 0.002, 0.0) == pytest.approx(99.8)
    assert effective_entry_price(raw, "SHORT", 0.0, 0.004) == pytest.approx(99.8)


def test_exit_price_adjusted_against_trader():
    price = 105.0
    # Exiting a LONG (you sold): you receive less.
    assert exit_price_adjusted(price, "LONG", 0.002, 0.0, "TARGET") == pytest.approx(105.0 * (1 - 0.002))
    # Exiting a SHORT (you covered/bought back): you pay more.
    assert exit_price_adjusted(price, "SHORT", 0.002, 0.0, "STOP") == pytest.approx(105.0 * (1 + 0.002))


def test_zero_fees_no_change():
    raw = 100.0
    assert effective_entry_price(raw, "LONG", 0.0, 0.0) == pytest.approx(100.0)
    assert exit_price_adjusted(raw, "LONG", 0.0, 0.0, "STOP") == pytest.approx(100.0)