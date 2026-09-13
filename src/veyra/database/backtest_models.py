"""Backtest research persistence entities.

Kept separate from the live setup repository: a backtest run is reproducible
without mutating live setup state. We store run metadata, trades, and setup
records — NOT the candle dataset (candles stay in the Parquet candle store).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Integer,
    Float,
    String,
    Text,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
)
from sqlalchemy.orm import declarative_base, relationship

BacktestBase = declarative_base()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BacktestRunRow(BacktestBase):
    __tablename__ = "backtest_runs"

    id = Column(Integer, primary_key=True)
    run_key = Column(String(128), nullable=False, unique=True, index=True)
    symbol = Column(String(32), nullable=False, index=True)
    timeframe = Column(String(8), nullable=False, index=True)
    start_time = Column(BigInteger, nullable=False)
    end_time = Column(BigInteger, nullable=False)
    candle_count = Column(Integer, nullable=False, default=0)
    config_snapshot_json = Column(Text, nullable=False, default="{}")
    execution_json = Column(Text, nullable=False, default="{}")
    split_json = Column(Text, nullable=False, default="{}")
    strategy_version = Column(String(64), nullable=False, default="")
    engine_version = Column(String(64), nullable=False, default="")
    dataset_hash = Column(String(64), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=utcnow)

    trades = relationship("BacktestTradeRow", back_populates="run")


class BacktestTradeRow(BacktestBase):
    __tablename__ = "backtest_trades"
    __table_args__ = (
        Index("ix_bt_trade_symbol_tf", "symbol", "timeframe"),
    )

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("backtest_runs.id"), nullable=False, index=True)
    trade_id = Column(String(64), nullable=False)
    setup_key = Column(String(128), nullable=False)
    symbol = Column(String(32), nullable=False)
    timeframe = Column(String(8), nullable=False)
    setup_type = Column(String(32), nullable=False)
    regime = Column(String(16), nullable=False)
    score = Column(Integer, nullable=False, default=0)
    score_normalized = Column(Integer, nullable=False, default=0)
    side = Column(String(8), nullable=False)
    detection_ts = Column(BigInteger, nullable=False)
    qualification_ts = Column(BigInteger, nullable=False)
    entry_ts = Column(BigInteger, nullable=False)
    entry_price = Column(Float, nullable=False)
    invalidation = Column(Text, nullable=True)
    exit_ts = Column(BigInteger, nullable=False)
    exit_price = Column(Float, nullable=False)
    exit_reason = Column(String(16), nullable=False)
    gross_return = Column(Float, nullable=False, default=0.0)
    fees = Column(Float, nullable=False, default=0.0)
    slippage = Column(Float, nullable=False, default=0.0)
    net_return = Column(Float, nullable=False, default=0.0)
    holding_bars = Column(Integer, nullable=False, default=0)
    max_favorable_excursion = Column(Float, nullable=False, default=0.0)
    max_adverse_excursion = Column(Float, nullable=False, default=0.0)
    overlap = Column(Integer, nullable=False, default=0)

    run = relationship("BacktestRunRow", back_populates="trades")


class BacktestEventRow(BacktestBase):
    __tablename__ = "backtest_events"
    __table_args__ = (
        Index("ix_bt_event_run_ts", "run_id", "timestamp"),
    )

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("backtest_runs.id"), nullable=False, index=True)
    timestamp = Column(BigInteger, nullable=False)
    event_type = Column(String(32), nullable=False)
    symbol = Column(String(32), nullable=False)
    timeframe = Column(String(8), nullable=False)
    setup_key = Column(String(128), nullable=True)
    detail = Column(Text, nullable=True)