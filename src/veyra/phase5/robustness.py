"""Phase 5 robustness / cost-sensitivity analysis (Q8).

Re-derives net returns for the *already fixed* historical trade set under several
execution cost scenarios, WITHOUT re-running the engine. Because each
``BacktestTrade`` carries ``gross_return``, ``fees`` and ``slippage``, a scenario
is simply:

    net = gross_return - (fees_per_trade + slippage_per_trade)

Cost parameters (entry/exit fees and slippage) are expressed as round-trip
fractions applied as an absolute cost per unit, matching how the backtester
models them. The trade *decisions* are unchanged (we never optimise, we only
measure sensitivity), so this is a valid robustness probe of execution
assumptions rather than a re-fit.

Result is deliberately comparable to the base case: if the verdict/edge
flips when costs double or halve, the conclusions are cost-sensitive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from ..backtest import BacktestTrade, MetricsEngine


@dataclass
class RobustnessRow:
    scenario: str
    fees_pct: float
    slippage_pct: float
    avg_return: float
    win_rate: float
    expectancy: float
    profit_factor: float
    net_trades: int


@dataclass
class RobustnessResult:
    base: RobustnessRow
    scenarios: List[RobustnessRow] = field(default_factory=list)
    flips_sign: bool = False

    def to_dict(self) -> Dict[str, object]:
        def row(r: RobustnessRow) -> Dict[str, object]:
            return {
                "scenario": r.scenario,
                "fees_pct": r.fees_pct,
                "slippage_pct": r.slippage_pct,
                "avg_return": r.avg_return,
                "win_rate": r.win_rate,
                "expectancy": r.expectancy,
                "profit_factor": r.profit_factor,
            }

        return {
            "base": row(self.base),
            "scenarios": [row(r) for r in self.scenarios],
            "flips_sign": self.flips_sign,
        }


class RobustnessEngine:
    """Measure cost-sensitivity of a fixed historical trade set."""

    def __init__(self, metrics: MetricsEngine | None = None) -> None:
        self._metrics = metrics or MetricsEngine()

    def run(self, trades: List[BacktestTrade]) -> RobustnessResult:
        base_cost = _round_trip(trades)
        base = self._scenario("base", trades, *base_cost)

        scenarios = [
            self._scenario("base", trades, *base_cost),
            self._scenario("cost_x2", trades, base_cost[0] * 2, base_cost[1] * 2),
            self._scenario("cost_x1.5", trades, base_cost[0] * 1.5, base_cost[1] * 1.5),
            self._scenario("cost_half", trades, base_cost[0] * 0.5, base_cost[1] * 0.5),
            self._scenario("cost_zero", trades, 0.0, 0.0),
        ]

        result = RobustnessResult(base=base, scenarios=scenarios)
        # Flip detection: does the *sign* of per-trade expectancy change between
        # zero-cost and double-cost under an otherwise fixed trade set?
        signs = {_sig(s.expectancy) for s in scenarios}
        result.flips_sign = len(signs) > 1
        return result

    def _scenario(
        self,
        name: str,
        trades: List[BacktestTrade],
        fees_rt: float,
        slippage_rt: float,
    ) -> RobustnessRow:
        adj = _recompute_net(trades, fees_rt, slippage_rt)
        m = self._metrics.compute(adj, [], len(adj))
        return RobustnessRow(
            scenario=name,
            fees_pct=fees_rt,
            slippage_pct=slippage_rt,
            avg_return=m.trades.average_return,
            win_rate=m.trades.win_rate,
            expectancy=m.risk.expectancy,
            profit_factor=m.risk.profit_factor if m.risk.profit_factor != float("inf") else None,
            net_trades=len(adj),
        )


def _round_trip(trades: List[BacktestTrade]):
    """Round-trip fee+slippage cost *fraction* present in the set.

    ``BacktestTrade.fees``/``slippage`` are stored as absolute per-trade
    fractions (e.g. 0.1% + 0.1% entry/exit = 0.002), and
    ``net_return = gross_return - fees - slippage``. We return the modal/average
    fraction directly, NOT divided by price.
    """
    fees = [t.fees or 0.0 for t in trades]
    slip = [t.slippage or 0.0 for t in trades]
    return _avg(fees), _avg(slip)


def _recompute_net(trades, fees_rt, slip_rt):
    """Adjust net_return under a new round-trip cost; returns new trade list."""
    out = []
    for t in trades:
        cost = fees_rt + slip_rt
        new_t = BacktestTrade(
            trade_id=t.trade_id,
            setup_key=t.setup_key,
            symbol=t.symbol,
            timeframe=t.timeframe,
            setup_type=t.setup_type,
            regime=t.regime,
            score=t.score,
            score_normalized=t.score_normalized,
            side=t.side,
            detection_ts=t.detection_ts,
            qualification_ts=t.qualification_ts,
            entry_ts=t.entry_ts,
            entry_price=t.entry_price,
            invalidation=t.invalidation,
            exit_ts=t.exit_ts,
            exit_price=t.exit_price,
            exit_reason=t.exit_reason,
            gross_return=t.gross_return,
            fees=fees_rt,
            slippage=slip_rt,
            net_return=t.gross_return - cost,
            holding_bars=t.holding_bars,
            max_favorable_excursion=t.max_favorable_excursion,
            max_adverse_excursion=t.max_adverse_excursion,
            overlap=t.overlap,
        )
        out.append(new_t)
    return out


def _avg(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _sig(x: float) -> int:
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0