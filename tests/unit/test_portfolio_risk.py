"""Tests for the portfolio-level risk layer (cooldown, exposure, correlation)."""

import pytest

from veyra.paper.portfolio_risk import PortfolioCheckResult, PortfolioRiskPolicy


def _policy(**overrides):
    settings = type(
        "S",
        (),
        {
            "portfolio_max_exposure_pct": overrides.get("exposure", 0.20),
            "portfolio_correlation_threshold": overrides.get("threshold", 0.70),
            "portfolio_correlation_multiplier": overrides.get("corr_mult", 0.50),
            "portfolio_equity": overrides.get("equity", 10_000.0),
        },
    )()
    return PortfolioRiskPolicy(settings)


def _open(symbol="SOL/USDT", timeframe="4H", side="LONG", notional=400.0):
    return {"symbol": symbol, "timeframe": timeframe, "side": side, "notional": notional}


def test_same_market_is_rejected_even_on_clean_gate():
    p = _policy()
    book = [_open()]
    res = p.check(symbol="SOL/USDT", timeframe="4H", side="LONG",
                  notional=100.0, open_positions=book)
    assert res.approved is False
    assert res.reason == "market_cooldown"


def test_correlation_absent_does_not_scale():
    p = _policy()
    book = [_open("SOL/USDT")]
    res = p.check(symbol="BTC/USDT", timeframe="4H", side="LONG",
                  notional=100.0, open_positions=book, correlations={})
    assert res.approved is True
    assert res.size_multiplier == pytest.approx(1.0)
    assert res.reason == "portfolio_ok"


def test_same_direction_correlated_book_scales_down():
    p = _policy()
    book = [_open("BTC/USDT", notional=100.0)]
    corr = {"ETH/USDT": {"BTC/USDT": 0.85}}
    res = p.check(symbol="ETH/USDT", timeframe="4H", side="LONG",
                  notional=100.0, open_positions=book, correlations=corr)
    assert res.approved is True
    assert res.size_multiplier == pytest.approx(0.50)


def test_opposite_direction_correlation_is_ignored():
    p = _policy()
    book = [_open("BTC/USDT", notional=100.0, side="LONG")]
    corr = {"ETH/USDT": {"BTC/USDT": 0.85}}
    res = p.check(symbol="ETH/USDT", timeframe="4H", side="SHORT",
                  notional=100.0, open_positions=book, correlations=corr)
    assert res.approved is True
    assert res.size_multiplier == pytest.approx(1.0)


def test_exposure_cap_rejects_when_book_at_cap():
    # equity 10000 * 20% = 2000 cap; book already 2000.
    p = _policy()
    book = [_open("BTC/USDT", notional=2000.0)]
    res = p.check(symbol="ETH/USDT", timeframe="4H", side="LONG",
                  notional=100.0, open_positions=book)
    assert res.approved is False
    assert res.reason == "portfolio_exposure_cap"


def test_exposure_scales_down_when_approaching_cap():
    p = _policy()
    # cap 2000; book 1500 -> usable 500; candidate 1000 -> scaled to 50%.
    book = [_open("BTC/USDT", notional=1500.0)]
    res = p.check(symbol="ETH/USDT", timeframe="4H", side="LONG",
                  notional=1000.0, open_positions=book)
    assert res.approved is True
    assert res.size_multiplier == pytest.approx(0.5)


def test_open_book_flattens_ledgers():
    class Pos:
        symbol = "SOL/USDT"
        timeframe = "4H"
        side = "LONG"
        notional = 500.0

        def unclosed(self):
            return []

    class Ledger:
        def unclosed(self):
            return [Pos()]

    p = _policy()
    rows = p.open_book([Ledger(), Ledger()])
    assert len(rows) == 2
    assert rows[0] == {"symbol": "SOL/USDT", "timeframe": "4H",
                       "side": "LONG", "notional": 500.0}


def test_check_result_helpers():
    ok = PortfolioCheckResult.ok(0.5)
    assert ok.approved is True and ok.size_multiplier == 0.5
    rej = PortfolioCheckResult.reject("market_cooldown")
    assert rej.approved is False and rej.size_multiplier == 0.0