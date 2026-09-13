"""Tests for the Best Catch scanner (filter-first, rank-second)."""

import pytest

from veyra.alerts.forward import ForwardStats
from veyra.market.best_catch import BestCatchScanner, derive_mean_halves, ev_per_risk


def _stats(band, n, win_rate, mean_return, risk):
    return ForwardStats(band=band, n=n, win_rate=win_rate,
                        mean_return=mean_return, risk=risk)


class _Store:
    def load(self, symbol, timeframe):
        return None  # no candles: correlations empty, entry falls back to area mid


class _Ledger:
    def __init__(self, open_positions=None):
        self._open = open_positions or []

    def unclosed(self):
        return self._open


class _Scan:
    def __init__(self, setups_by_market=None):
        self._store = _Store()
        self._setups = setups_by_market or {}

    def live(self, symbol, timeframe, refresh=True):
        setups = self._setups.get((symbol, timeframe), [])
        return {"symbol": symbol, "timeframe": timeframe,
                "setups": setups, "live_price": 100.0}


class _Engine:
    def __init__(self, ledgers=None):
        self._ledgers = ledgers or {}

    def load(self, symbol, timeframe):
        return self._ledgers.get((symbol, timeframe), _Ledger())


def _setup(score=90, side="LONG", setup_type="TREND_CONTINUATION", invalidation=98.0,
           atr=None, area=(99.0, 101.0), scores=None, timestamp=1_700_000_000):
    return {
        "setup_type": setup_type, "side": side, "regime": "BULL",
        "overall_score": score,
        "scores": scores or {k: v for k, v in zip(
            ["trend", "structure", "pullback", "momentum", "volume", "volatility"],
            [25, 20, 12, 10, 5, 4])},
        "timestamp": timestamp,
        "interest_area": {"low": area[0], "high": area[1]} if area else None,
        "invalidation": invalidation, "reasoning": "t", "atr": atr,
    }


class FakeScanner(BestCatchScanner):
    """Bypasses alerts/candles: tests inject band statistics directly."""

    def __init__(self, net, gross, scan, engine, top_n=3, **kw):
        super().__init__(scan=scan, engine=engine, top_n=top_n, **kw)
        self._net = net
        self._gross = gross

    def _stats_for(self, symbol, timeframe):
        from veyra.alerts.forward import ForwardReturnModel

        return dict(self._net), dict(self._gross), ForwardReturnModel(None, horizon=86400)


def _scanner(net, gross, setups, ledgers=None, top_n=3):
    scan = _Scan(setups)
    engine = _Engine(ledgers or {})
    return FakeScanner(net, gross, scan, engine, top_n=top_n)


def test_derive_mean_halves_recovers_win_and_loss_pcts():
    st = _stats("CONVERGENT", n=80, win_rate=0.60, mean_return=0.05, risk=0.025)
    mw, ml = derive_mean_halves(st)
    assert mw == pytest.approx(0.10)
    assert ml == pytest.approx(0.025)


def test_ev_per_risk_formula():
    assert ev_per_risk(0.58, 0.10, 0.025, 0.003, 0.20) == pytest.approx(0.2225)
    assert ev_per_risk(0.80, 0.02, 0.004, 0.003, 0.02) == pytest.approx(0.61)


def test_best_catch_filters_ineligible_setups():
    """A high-dollar setup whose band fails the posterior gate never appears."""
    # Win rate 0.40 -> posterior (10 + 40) / 120 = 0.4167 < 0.55, gate rejects.
    net = {"CONVERGENT": _stats("CONVERGENT", n=100, win_rate=0.40,
                                mean_return=0.05, risk=0.01)}
    gross = {"CONVERGENT": _stats("CONVERGENT", n=100, win_rate=0.40,
                                  mean_return=0.05, risk=0.01)}
    scanner = _scanner(net, gross, {("SOL/USDT", "4H"): [_setup(score=90)]})
    result = scanner.scan(datasets=[{"symbol": "SOL/USDT", "timeframe": "4H"}])
    assert result.empty is True
    assert result.cards == []


def test_best_catch_empty_state():
    scanner = _scanner({}, {}, {("SOL/USDT", "4H"): []})
    result = scanner.scan(datasets=[{"symbol": "SOL/USDT", "timeframe": "4H"}])
    assert result.empty is True
    assert result.note == "No qualifying catch right now"
    assert result.cards == []


def test_best_catch_ranks_by_ev_per_risk_not_magnitude():
    # SOL: larger dollar move (net 4.7% at the reference amount) but a wide 20%
    #      stop and 0.60 win rate -> poor risk-adjusted ratio.
    # ETH: smaller dollar move (net 1.34%) but tight 2% stop + 0.85 win rate.
    net = {
        "CONVERGENT": _stats("CONVERGENT", n=80, win_rate=0.60,
                             mean_return=0.047, risk=0.025),
        "DIRECTIONAL": _stats("DIRECTIONAL", n=60, win_rate=0.85,
                              mean_return=0.0134, risk=0.004),
    }
    gross = {
        "CONVERGENT": _stats("CONVERGENT", n=80, win_rate=0.60,
                             mean_return=0.05, risk=0.025),
        "DIRECTIONAL": _stats("DIRECTIONAL", n=60, win_rate=0.85,
                              mean_return=0.0164, risk=0.004),
    }
    setups = {
        ("SOL/USDT", "4H"): [_setup(score=92, invalidation=80.0)],   # ~20% stop
        ("ETH/USDT", "4H"): [_setup(score=58, invalidation=98.0)],   # ~2% stop
    }
    scanner = _scanner(net, gross, setups, top_n=2)
    result = scanner.scan(datasets=[
        {"symbol": "SOL/USDT", "timeframe": "4H"},
        {"symbol": "ETH/USDT", "timeframe": "4H"},
    ])
    cards = result.cards
    assert len(cards) == 2
    by_market = {c.market: c for c in cards}
    sol, eth = by_market["SOL/USDT"], by_market["ETH/USDT"]
    # The higher-dollar candidate must NOT rank first.
    assert eth.est_make_usd < sol.est_make_usd
    assert eth.ev_per_risk > sol.ev_per_risk
    assert cards[0].market == "ETH/USDT"
    assert eth.n_observed_at_decision_time == 60


def test_best_catch_respects_portfolio_risk_rejection():
    """A setup blocked by the market cooldown must not surface, even if the
    posterior gate and band ladder otherwise pass it."""
    net = {"CONVERGENT": _stats("CONVERGENT", n=80, win_rate=0.60,
                                mean_return=0.047, risk=0.01)}
    gross = {"CONVERGENT": _stats("CONVERGENT", n=80, win_rate=0.60,
                                  mean_return=0.05, risk=0.01)}
    open_pos = type("P", (), {"symbol": "SOL/USDT", "timeframe": "4H",
                              "side": "LONG", "notional": 500.0})()
    ledgers = {("SOL/USDT", "4H"): _Ledger([open_pos])}
    scanner = _scanner(net, gross, {("SOL/USDT", "4H"): [_setup(score=90)]},
                       ledgers=ledgers)
    result = scanner.scan(datasets=[{"symbol": "SOL/USDT", "timeframe": "4H"}])
    assert result.empty is True
    assert result.cards == []


def test_card_shows_evidence_prominently():
    net = {"CONVERGENT": _stats("CONVERGENT", n=41, win_rate=0.80,
                                mean_return=0.01, risk=0.002)}
    gross = {"CONVERGENT": _stats("CONVERGENT", n=41, win_rate=0.80,
                                  mean_return=0.012, risk=0.002)}
    scanner = _scanner(net, gross, {("SOL/USDT", "4H"): [_setup(score=88)]})
    result = scanner.scan(datasets=[{"symbol": "SOL/USDT", "timeframe": "4H"}])
    assert result.empty is False
    card = result.cards[0]
    assert card.n_observed_at_decision_time == 41
    assert card.posterior_win_rate >= 0.55
    assert card.portfolio_risk_check == "passed"
    assert card.eligibility_reason == "eligible"
    d = card.to_dict()
    assert d["market"] == "SOL/USDT"
    assert d["band"] == "CONVERGENT"
    # Posterior pools 0.80 observed win rate toward the (10, 10) prior.
    assert d["posterior_win_rate"] == pytest.approx((10 + 41 * 0.80) / 61.0, abs=1e-3)
    assert d["n_observed_at_decision_time"] == 41
    assert "component_breakdown" in d