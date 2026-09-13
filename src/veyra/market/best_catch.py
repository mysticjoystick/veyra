"""Best Catch — the single highest-conviction, currently-actionable setup.

A surfacing/ranking layer over the EXISTING setup detection. It rescans every
market in the live universe (the same 12 Stage-4 datasets the paper engine
checks), and instead of sorting by raw dollar magnitude (which lets a rare,
low-probability, high-magnitude row look as confident as an evidence-backed
setup), it:

  1. FILTERS FIRST — a setup is only *considered* when ALL of:
     * band != SCANNED (on the canonical ladder)
     * the Bayesian posterior gate passes with the band's real observed history
       (trade_eligible == true, evaluated the SAME way the paper engine does —
       no cold-start bypass, prior win-rate floor 0.55)
     * the portfolio risk layer approves it (cooldown / exposure cap /
       correlation sizing are applied exactly as at entry)
     * posterior_win_rate >= 0.55
     * the observed evidence count n is present for display
     Anything failing a rule is excluded entirely — never shown lower, never
     shown greyed out as a fallback pick. Zero qualified setups is the honest
     answer: the result carries an explicit empty state.
  2. RANKS SECOND by risk-adjusted edge, not magnitude:

       ev_per_risk = (posterior_win_rate * mean_win_pct
                      - (1 - posterior_win_rate) * mean_loss_pct
                      - round_trip_cost) / stop_distance_pct

     Ties break by higher posterior_win_rate, then more observed evidence.

Best Catch NEVER loosens the quality gate, cold-start rule or portfolio caps;
it never changes position sizing (the dollar figures ESTIMATE P&L at the
reference ``amount`` and are display-only); and it is a re-ranking only — it
does not place orders or bypass any execution path in paper/live.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field as dc_field
from typing import Dict, List, Optional

from ..alerts.costs import CostModel
from ..alerts.forward import ForwardReturnModel, _field, horizon_for
from ..alerts.live import LiveScan
from ..config import Settings, get_settings
from ..paper.live import (
    _STAGE4_DATASETS,
    _band_for,
    _cold_scale,
    _next_open_or_area,
    _stop_from_setup,
    LivePaperEngine,
)
from ..paper.portfolio_risk import PortfolioRiskPolicy
from ..paper.quality import MIN_POSTERIOR_WIN_RATE, evaluate_eligibility, posterior_stats

logger = logging.getLogger(__name__)


# ── Output model ------------------------------------------------------------

@dataclass(frozen=True)
class BestCatchCard:
    """One fully-transparent, fully-filtered candidate."""

    market: str
    timeframe: str
    band: str
    setup_type: str
    direction: str
    raw_score: int
    posterior_win_rate: float
    n_observed_at_decision_time: int
    ev_per_risk: float
    est_make_usd: float
    est_risk_usd: float
    portfolio_risk_check: str = "passed"
    eligibility_reason: str = "eligible"
    component_breakdown: Dict[str, int] = dc_field(default_factory=dict)
    # Transparency extras (not required by the spec, never used for ranking).
    mean_win_pct: Optional[float] = None
    mean_loss_pct: Optional[float] = None
    round_trip_cost: float = 0.003
    stop_distance_pct: Optional[float] = None
    entry_price: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "market": self.market,
            "timeframe": self.timeframe,
            "band": self.band,
            "setup_type": self.setup_type,
            "direction": self.direction,
            "raw_score": self.raw_score,
            "posterior_win_rate": round(self.posterior_win_rate, 3),
            "n_observed_at_decision_time": self.n_observed_at_decision_time,
            "ev_per_risk": round(self.ev_per_risk, 2),
            "est_make_usd": round(self.est_make_usd),
            "est_risk_usd": round(self.est_risk_usd),
            "portfolio_risk_check": self.portfolio_risk_check,
            "eligibility_reason": self.eligibility_reason,
            "component_breakdown": dict(self.component_breakdown),
            "mean_win_pct": round(self.mean_win_pct, 4) if self.mean_win_pct is not None else None,
            "mean_loss_pct": round(self.mean_loss_pct, 4) if self.mean_loss_pct is not None else None,
            "round_trip_cost": self.round_trip_cost,
            "stop_distance_pct": round(self.stop_distance_pct, 4) if self.stop_distance_pct is not None else None,
            "entry_price": round(self.entry_price, 6) if self.entry_price is not None else None,
        }


@dataclass(frozen=True)
class BestCatchResult:
    """Ranked top-N cards, or the explicit empty state."""

    cards: List[BestCatchCard]
    scanned_markets: int = 0
    empty: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "cards": [c.to_dict() for c in self.cards],
            "scanned_markets": self.scanned_markets,
            "empty": self.empty,
            "note": self.note,
            "simulation": True,
        }


EMPTY_NOTE = "No qualifying catch right now"


# ── Rank-only core (pure, unit-testable) ------------------------------------

def derive_mean_halves(st) -> tuple:
    """Split a band's forward stats into observed mean win/loss PCTs.

    Recovered exactly from the aggregate moments of the same distribution the
    stats describe: mean_return = wr*mean_win - (1-wr)*mean_loss and risk is the
    mean magnitude of losses, so

        mean_win  = (mean_return + (1 - wr) * risk) / wr   (when wr > 0)
        mean_loss = risk (0.0 when the band has never lost)

    Returns (mean_win, mean_loss); both None when the band has no wins at all.
    """
    if st is None:
        return None, None
    wr = st.win_rate or 0.0
    risk = st.risk
    mean = st.mean_return
    if mean is None:
        return None, None
    if wr <= 0:
        return None, (risk if risk is not None else 0.0)
    mean_loss = risk if risk is not None else 0.0
    mean_win = (mean + (1.0 - wr) * mean_loss) / wr
    return mean_win, mean_loss


def ev_per_risk(posterior_wr: float, mean_win: float, mean_loss: float,
                round_trip: float, stop_pct: Optional[float]) -> Optional[float]:
    """Risk-adjusted edge per unit of stop distance (returns None on bad stop)."""
    if stop_pct is None or stop_pct <= 1e-6:
        return None
    if mean_win is None or mean_loss is None or posterior_wr is None:
        return None
    ev = posterior_wr * mean_win - (1.0 - posterior_wr) * mean_loss - round_trip
    return ev / stop_pct


# ── Scanner ---------------------------------------------------------------

class BestCatchScanner:
    """Rescan the full live universe and rank the top-N catches."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        scan: Optional[LiveScan] = None,
        engine: Optional[LivePaperEngine] = None,
        top_n: Optional[int] = None,
        amount: Optional[float] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._scan = scan or LiveScan(self._settings)
        self._engine = engine or LivePaperEngine(self._settings, scan=self._scan)
        self._cost = CostModel()
        self._top_n = int(top_n if top_n is not None else self._settings.best_catch_top_n)
        self._amount = float(amount if amount is not None else self._settings.best_catch_amount)
        self._policy = PortfolioRiskPolicy(self._settings)

    # -- seams (overridable in tests) -------------------------------------

    def _stats_for(self, symbol: str, timeframe: str):
        """Band evidence for a market: (net_stats, gross_stats, forward_model).

        Net stats drive the eligibility gate exactly as the paper engine reads
        them; gross stats feed the ev_per_risk formula (which subtracts the
        round-trip cost itself). Both come from the alerts cache, so this is a
        re-ranking, not a new detection pass.
        """
        alerts = self._engine._market_alerts(symbol, timeframe)  # noqa: SLF001
        candles = self._scan._store.load(symbol, timeframe)  # noqa: SLF001
        horizon = horizon_for(timeframe)
        model = ForwardReturnModel(candles, horizon=horizon)
        net = model.stats_by_band(alerts, field=_field("net", horizon))
        gross = model.stats_by_band(alerts, field=_field("realized", horizon))
        return net, gross, model

    # -- the scan ----------------------------------------------------------

    def scan(self, refresh: bool = True, datasets: Optional[List[dict]] = None) -> BestCatchResult:
        markets = datasets or _STAGE4_DATASETS

        correlations: Dict[str, Dict[str, float]] = {}
        try:
            correlations = self._policy.compute_correlations(
                markets, store=self._scan._store, days=30
            )
        except Exception as exc:  # noqa: BLE001 - offline/short history: skip corr
            logger.warning("best-catch correlations unavailable: %s", exc)
        try:
            open_book = self._policy.open_book([self._engine.load(m["symbol"], m["timeframe"]) for m in markets])
        except Exception as exc:  # noqa: BLE001 - ledger unreadable: empty book
            logger.warning("best-catch open book unavailable: %s", exc)
            open_book = []

        cards: List[BestCatchCard] = []
        scanned = 0
        for ds in markets:
            symbol, timeframe = ds["symbol"], ds["timeframe"]
            try:
                live = self._scan.live(symbol, timeframe, refresh=refresh)
                if live.get("error"):
                    continue
                setups = [
                    s for s in (live.get("setups") or [])
                    if _band_for(int(s.get("overall_score") or 0)) != "SCANNED"
                ]
                net, gross, model = self._stats_for(symbol, timeframe)
                for setup in setups:
                    card = self._card(
                        setup, symbol, timeframe, net, gross, model,
                        open_book=open_book, correlations=correlations,
                    )
                    if card is not None:
                        cards.append(card)
                scanned += 1
            except Exception as exc:  # noqa: BLE001 - one market must not fault the scan
                logger.warning("best-catch scan failed for %s %s: %s", symbol, timeframe, exc)

        cards.sort(key=lambda c: (-c.ev_per_risk, -c.posterior_win_rate, -c.n_observed_at_decision_time))
        top = cards[: max(0, self._top_n)]

        if not top:
            return BestCatchResult(cards=[], scanned_markets=scanned, empty=True, note=EMPTY_NOTE)
        return BestCatchResult(cards=top, scanned_markets=scanned, empty=False,
                               note=f"{len(top)} qualifying catch(es)" if len(top) > 1 else "1 qualifying catch")

    # -- candidate evaluation ----------------------------------------------

    def _card(
        self,
        setup: dict,
        symbol: str,
        timeframe: str,
        net_stats: Dict[str, object],
        gross_stats: Dict[str, object],
        model: ForwardReturnModel,
        open_book: List[dict],
        correlations: Dict[str, Dict[str, float]],
    ) -> Optional[BestCatchCard]:
        score = int(setup.get("overall_score") or 0)
        band = _band_for(score)
        if band == "SCANNED":
            return None  # filter 1: raw_score_below_band_floor

        st = net_stats.get(band)
        # Filter 5: evidence count must be present for display.
        if st is None or not st.n:
            return None
        n_observed = st.n

        # Filter 2: the real posterior gate (same contract as paper/live.py).
        elig = evaluate_eligibility(score, st)
        if not elig["trade_eligible"]:
            return None

        pm, pw, _ = posterior_stats(st)
        # Filter 4: explicit posterior win-rate floor (gate already implies it).
        if pw < MIN_POSTERIOR_WIN_RATE:
            return None

        # Entry + stop estimates (same helpers the engine would use at entry).
        s2 = dict(setup)
        s2["time"] = timeframe
        entry_price = _next_open_or_area(s2, model)
        if entry_price is None or entry_price <= 0:
            return None
        stop_price = _stop_from_setup(
            str(setup.get("side") or "LONG"), float(entry_price),
            setup.get("invalidation"),
            atr=setup.get("atr"),
            atr_multiplier=self._settings.paper_atr_stop_multiplier,
            fallback_pct=self._settings.paper_stop_fallback_pct,
        )
        stop_pct = (abs(entry_price - stop_price) / entry_price
                    if stop_price and entry_price > 0 else None)

        # Weighted score breakdown for the card (mirror the engine's inputs).
        breakdown = {k: int(v) for k, v in (setup.get("scores") or {}).items()}

        # ev_per_risk on GROSS band stats; cost subtracted explicitly (spec formula).
        gst = gross_stats.get(band)
        mean_win, mean_loss = derive_mean_halves(gst)
        evpr = ev_per_risk(pw, mean_win, mean_loss, self._cost.round_trip, stop_pct)
        if evpr is None:
            return None

        # Risk-budget notional the ENGINE would open, used only for the
        # portfolio-eligibility check (mirrors entry-time sizing exactly).
        risk_usd = self._settings.paper_risk_per_trade * _cold_scale(
            n_observed, thresholds=self._settings.paper_cold_tier_thresholds
        )
        check_notional = self._amount
        if stop_pct and stop_pct > 1e-6:
            check_notional = min(self._amount, risk_usd / stop_pct)
        if check_notional <= 0:
            return None

        # Filter 3: portfolio risk layer must approve (cooldown/exposure/corr).
        result = self._policy.check(
            symbol=symbol,
            timeframe=timeframe,
            side=str(setup.get("side") or "LONG"),
            notional=check_notional,
            open_positions=open_book,
            correlations=correlations,
        )
        if not result.approved:
            return None  # blocked by portfolio_risk; excluded entirely

        # Dollar figures are estimated AT THE REFERENCE AMOUNT (not the risk-
        # scaled notional), so magnitude stays visible but never drives ranking.
        mean_return = st.mean_return if st.mean_return is not None else 0.0
        return BestCatchCard(
            market=symbol,
            timeframe=timeframe,
            band=band,
            setup_type=str(setup.get("setup_type") or ""),
            direction=str(setup.get("side") or "LONG"),
            raw_score=score,
            posterior_win_rate=pw,
            n_observed_at_decision_time=n_observed,
            ev_per_risk=evpr,
            est_make_usd=self._amount * mean_return,
            est_risk_usd=-self._amount * stop_pct if stop_pct else 0.0,
            portfolio_risk_check="passed",
            eligibility_reason=elig["eligibility_reason"],
            component_breakdown=breakdown,
            mean_win_pct=mean_win,
            mean_loss_pct=mean_loss,
            round_trip_cost=self._cost.round_trip,
            stop_distance_pct=stop_pct,
            entry_price=entry_price if entry_price > 0 else None,
        )