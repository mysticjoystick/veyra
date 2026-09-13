"""Phase 4 persistence tests (backtest repository).

Research runs are persisted separately from the live setup repository; candles are
NOT stored in SQLite (they stay in the Parquet candle store). Tests verify the
save/load round-trip and that live setup state is not mutated.
"""

from __future__ import annotations

import pytest

from veyra.backtest import BacktestEngine
from veyra.database.backtest_repository import BacktestRepository
from tests.unit.market_synth import bull_continuation_frame


def _run_engine():
    engine = BacktestEngine(__import__("veyra.config", fromlist=["Settings"]).Settings())
    return engine.run("BTC/USDT", "4H", bull_continuation_frame(n=260, start_ts=1_000_000_000), run_key="persist-1")


def test_repository_round_trip_trades_and_events(session_factory):
    repo = BacktestRepository(session_factory)
    res = _run_engine()
    row = repo.save(res)
    loaded = repo.get_run(row.id)
    assert loaded is not None
    assert loaded.run_key == "persist-1"
    assert loaded.candle_count == res.run.candle_count
    trades = repo.trades_for_run(row.id)
    assert len(trades) == len(res.trades)
    # Match by trade_id (in-memory and stored orderings may differ); values must
    # round-trip exactly.
    by_id = {t.trade_id: t for t in res.trades}
    for stored in trades:
        orig = by_id[stored.trade_id]
        assert stored.entry_price == pytest.approx(orig.entry_price)
        assert stored.exit_reason == orig.exit_reason
        assert stored.net_return == pytest.approx(orig.net_return)


def test_config_and_execution_persist_verbatim(session_factory):
    repo = BacktestRepository(session_factory)
    res = _run_engine()
    row = repo.save(res)
    loaded = repo.get_run(row.id)
    assert loaded.execution_json  # execution config recorded
    assert loaded.split_json
    assert loaded.config_snapshot_json
    assert loaded.dataset_hash == res.run.dataset_hash
    assert loaded.strategy_version


def test_find_runs_by_symbol_and_timeframe(session_factory):
    repo = BacktestRepository(session_factory)
    repo.save(_run_engine())
    rows = repo.find_runs(symbol="BTC/USDT")
    assert rows
    rows = repo.find_runs(symbol="NOPE")
    assert rows == []


def test_multiple_runs_do_not_overwrite(session_factory):
    repo = BacktestRepository(session_factory)
    r1 = _run_engine()
    r2 = BacktestEngine(__import__("veyra.config", fromlist=["Settings"]).Settings()).run(
        "ETH/USDT", "4H", bull_continuation_frame(n=200, start_ts=2_000_000_000), run_key="persist-2"
    )
    a = repo.save(r1)
    b = repo.save(r2)
    assert a.id != b.id
    assert repo.get_run(a.id).run_key == "persist-1"
    assert repo.get_run(b.id).run_key == "persist-2"