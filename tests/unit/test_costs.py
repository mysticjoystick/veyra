"""Tests for the Step-2 trading-cost model: fees + slippage (net returns)."""

from __future__ import annotations

import pytest

from veyra.alerts.costs import CostModel, DEFAULT_FEE_PER_SIDE, DEFAULT_SLIPPAGE_PER_SIDE


def test_default_round_trip_is_0p3_percent():
    cost = CostModel()
    assert cost.round_trip == pytest.approx(0.003)


def test_net_return_subtracts_full_round_trip():
    cost = CostModel()
    assert cost.net_return(0.05) == pytest.approx(0.047)
    assert cost.net_return(None) is None


def test_small_gross_becomes_negative_net_honest():
    # A +0.1% gross move is a net loss after 0.3% round-trip cost.
    cost = CostModel()
    assert cost.net_return(0.001) == pytest.approx(-0.002)


def test_custom_costs_override_defaults():
    cost = CostModel(fee_per_side=0.002, slippage_per_side=0.0)
    assert cost.round_trip == pytest.approx(0.004)
    assert cost.net_return(0.01) == pytest.approx(0.006)


def test_to_dict_exposes_assumptions():
    d = CostModel().to_dict()
    assert d["round_trip"] == pytest.approx(0.003)
    assert d["fee_per_side"] == DEFAULT_FEE_PER_SIDE
    assert d["slippage_per_side"] == DEFAULT_SLIPPAGE_PER_SIDE