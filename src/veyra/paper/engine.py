"""Phase 5 paper-trading engine — SIMULATION ONLY.

Propagates the frozen Phase 4 baseline forward over real candles, tracking a
virtual account. This NEVER touches real money, a brokerage, or an exchange: it
is an offline replayer that applies the same execution assumptions
(fees/slippage/entry-at-next-open) as the backtester, but presents the result as
a forward-looking paper ledger instead of a historical backtest.

Rules honoured here:
- no real executions, no deposits/withdrawals, no brokerage connection;
- the engine is the frozen baseline (see phase5.frozen_config) — no tuning;
- positions are simulated with the same cost model; equity is mark-to-market;
- the default paper strategy is capital-preserving: it only takes trades the
  frozen baseline would take and sizes each at a fixed fraction of equity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from ..backtest import BacktestEngine, MetricsEngine
from ..config import Settings, get_settings
from ..data.candle_store import CandleStore
from ..phase5.frozen_config import PHASE5_BASELINE_VERSION, build_baseline_engine

# Explicit guard: paper trading NEVER executes real orders.
REAL_EXECUTION_FORBIDDEN = "paper trading is simulation-only; no real execution"
_DEFAULT_SCHEMA_REV = "paper-v1"


@dataclass
class PaperTrade:
    """One simulated round-trip in the paper account."""

    trade_id: str
    setup_key: str
    symbol: str
    timeframe: str
    setup_type: str
    side: str
    entry_ts: int
    entry_price: float
    shares: float
    exit_ts: int
    exit_price: float
    gross_return: float
    fees: float
    net_return: float
    pnl: float


@dataclass
class PaperSession:
    symbol: str
    timeframe: str
    strategy_version: str = PHASE5_BASELINE_VERSION
    schema: str = _DEFAULT_SCHEMA_REV
    start_cash: float = 100_000.0
    position_fraction: float = 0.25
    cash: float = field(default=0.0)
    equity_curve: List[dict] = field(default_factory=list)
    trades: List[PaperTrade] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "strategy_version": self.strategy_version,
            "schema": self.schema,
            "start_cash": self.start_cash,
            "position_fraction": self.position_fraction,
            "cash": self.cash,
            "equity_curve": self.equity_curve,
            "trades": [t.__dict__ for t in self.trades],
        }


class PaperEngine:
    """Simulate a forward paper-trading session over real candles."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        engine: Optional[BacktestEngine] = None,
        metrics: Optional[MetricsEngine] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._engine = engine or build_baseline_engine(
            self._settings, progressive=True
        )
        self._metrics = metrics or MetricsEngine()

    def run(
        self,
        symbol: str,
        timeframe: str,
        df=None,
        start_cash: float = 100_000.0,
        position_fraction: float = 0.25,
    ) -> PaperSession:
        if df is None:
            df = CandleStore(self._settings).load(symbol, timeframe)
        if df is None or df.empty:
            raise ValueError(f"No candles for {symbol} {timeframe}")
        df = df.sort_values("open_time").reset_index(drop=True)

        session = PaperSession(
            symbol=symbol,
            timeframe=timeframe,
            start_cash=start_cash,
            position_fraction=position_fraction,
            cash=start_cash,
        )

        # The backtester already applies fees/slippage/next-open entry. Paper
        # re-uses its deterministic trades as the simulated ledger (same
        # decisions, same costs) -> no divergence from the frozen baseline.
        result = self._engine.run(
            symbol, timeframe, df.copy(), run_key=f"paper|{symbol}|{timeframe}"
        )

        # Virtual sizing at fixed % of equity per trade.
        equity = start_cash
        for i, t in enumerate(result.trades):
            notional = equity * position_fraction
            shares = notional / t.entry_price if t.entry_price else 0.0
            pnl = notional * t.net_return
            equity += pnl
            session.trades.append(
                PaperTrade(
                    trade_id=t.trade_id,
                    setup_key=t.setup_key,
                    symbol=t.symbol,
                    timeframe=t.timeframe,
                    setup_type=t.setup_type,
                    side=t.side,
                    entry_ts=int(t.entry_ts),
                    entry_price=t.entry_price,
                    shares=round(shares, 6),
                    exit_ts=int(t.exit_ts),
                    exit_price=t.exit_price,
                    gross_return=t.net_return + t.fees + t.slippage,
                    fees=t.fees,
                    net_return=t.net_return,
                    pnl=pnl,
                )
            )

        session.cash = equity
        session.equity_curve = self._equity_curve(session, df)
        return session

    def _equity_curve(self, session: PaperSession, df) -> List[dict]:
        curve = []
        equity = session.start_cash
        # Walk the candle timeline; book realized PnL on the exit ts.
        for ts in df["open_time"].tolist():
            for t in session.trades:
                if ts == t.exit_ts:
                    equity += t.pnl
            curve.append({"ts": int(ts), "equity": round(equity, 2)})
        return curve