"""Backtest persistence repository (research series).

Persists/serialises a completed backtest run, its trades, and its events so a
run is reproducible from stored metadata WITHOUT re-querying live setup state or
re-storing candles (candles remain in the CandleStore).
"""

from __future__ import annotations

import json
from typing import List, Optional

from sqlalchemy.orm import Session

from ..backtest.models import (
    BacktestEvent,
    BacktestRun,
    BacktestTrade,
    ExecutionConfig,
    SimulationResult,
    SplitConfig,
)
from .backtest_models import (
    BacktestBase,
    BacktestEventRow,
    BacktestRunRow,
    BacktestTradeRow,
)


class BacktestRepository:
    def __init__(self, session_cls) -> None:
        self._session_cls = session_cls

    def save(self, result: SimulationResult) -> BacktestRunRow:
        run = result.run
        with self._session_cls() as session:
            row = BacktestRunRow(
                run_key=run.run_key,
                symbol=run.symbol,
                timeframe=run.timeframe,
                start_time=run.start_time,
                end_time=run.end_time,
                candle_count=run.candle_count,
                config_snapshot_json=json.dumps(run.config_snapshot, default=str),
                execution_json=json.dumps(run.execution.to_dict(), default=str),
                split_json=json.dumps(run.split.to_dict(), default=str),
                strategy_version=run.strategy_version,
                engine_version=run.engine_version,
                dataset_hash=run.dataset_hash,
            )
            session.add(row)
            session.flush()
            for t in result.trades:
                session.add(self._trade_row(row.id, t))
            for e in result.events:
                session.add(
                    BacktestEventRow(
                        run_id=row.id,
                        timestamp=e.timestamp,
                        event_type=e.event_type,
                        symbol=e.symbol,
                        timeframe=e.timeframe,
                        setup_key=e.setup_key,
                        detail=e.detail,
                    )
                )
            session.commit()
            return row

    @staticmethod
    def _trade_row(run_id: int, t: BacktestTrade) -> BacktestTradeRow:
        return BacktestTradeRow(
            run_id=run_id,
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
            fees=t.fees,
            slippage=t.slippage,
            net_return=t.net_return,
            holding_bars=t.holding_bars,
            max_favorable_excursion=t.max_favorable_excursion,
            max_adverse_excursion=t.max_adverse_excursion,
            overlap=1 if t.overlap else 0,
        )

    def get_run(self, run_id: int) -> Optional[BacktestRunRow]:
        with self._session_cls() as session:
            return session.query(BacktestRunRow).filter_by(id=run_id).one_or_none()

    def trades_for_run(self, run_id: int) -> List[BacktestTradeRow]:
        with self._session_cls() as session:
            return (
                session.query(BacktestTradeRow)
                .filter_by(run_id=run_id)
                .order_by(BacktestTradeRow.entry_ts.asc())
                .all()
            )

    def find_runs(self, symbol: Optional[str] = None, timeframe: Optional[str] = None) -> List[BacktestRunRow]:
        with self._session_cls() as session:
            q = session.query(BacktestRunRow)
            if symbol:
                q = q.filter(BacktestRunRow.symbol == symbol)
            if timeframe:
                q = q.filter(BacktestRunRow.timeframe == timeframe)
            return q.order_by(BacktestRunRow.start_time.asc()).all()

    def to_domain(self, row: BacktestRunRow) -> BacktestRun:
        return BacktestRun(
            run_key=row.run_key,
            symbol=row.symbol,
            timeframe=row.timeframe,
            start_time=row.start_time,
            end_time=row.end_time,
            candle_count=row.candle_count,
            config_snapshot=json.loads(row.config_snapshot_json or "{}"),
            execution=ExecutionConfig.from_dict(json.loads(row.execution_json or "{}")),
            split=SplitConfig(**json.loads(row.split_json or "{}")),
            strategy_version=row.strategy_version,
            engine_version=row.engine_version,
            dataset_hash=row.dataset_hash,
        )