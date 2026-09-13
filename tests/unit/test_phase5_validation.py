"""Unit tests for the Phase 5 validation orchestration.

Covers:
  - validate.Phase5Validator: single-run overall/breakdown/period outputs
  - validate.FIXED_SCORE_BUCKETS bucket scheme
  - calibration.CalibrationEngine: score-usefulness measurement
  - gate.Phase5Gates: Gates A-G verdict logic

These use small synthetic frames so they are fast; real-data validation is a
separate (slow) step and is not part of the unit suite.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from veyra.backtest import BacktestTrade, MetricsEngine
from veyra.config import Settings
from veyra.phase5 import (
    FIXED_SCORE_BUCKETS,
    Build,
    CalibrationEngine,
    CalibrationResult,
    Evidence,
    Phase5Gates,
    Phase5Validator,
    GateStatus,
    Verdict,
)


@pytest.fixture
def settings():
    return Settings()


def _frame(n=240, seed=7, start=1_000_000_000, interval=14400):
    rng = np.random.default_rng(seed)
    close = np.cumsum(rng.normal(0.4, 0.6, n)) + 100.0
    open_ = close - rng.normal(0, 0.2, n)
    high = np.maximum(open_, close) + 1.0
    low = np.minimum(open_, close) - 1.0
    return pd.DataFrame(
        {
            "open_time": start + np.arange(n, dtype=np.int64) * interval,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(n, 1000.0),
        }
    )


def _make_trades(n, win_rate, score=50, end_ts=1_000_000_000 + 1000):
    """Build ``n`` BacktestTrades with the desired win rate."""
    trades = []
    wins = int(round(n * win_rate))
    for i in range(n):
        is_win = i < wins
        net = (0.05 - 0.004) if is_win else (-0.03 - 0.004)
        trades.append(
            BacktestTrade(
                trade_id=f"t{i}",
                setup_key=f"s{i}",
                symbol="BTC/USDT",
                timeframe="1D",
                setup_type="EPA",
                regime="bull",
                score=score,
                score_normalized=score,
                side="LONG",
                detection_ts=end_ts - 100,
                qualification_ts=end_ts - 50,
                entry_ts=end_ts - 50,
                entry_price=100.0,
                invalidation="",
                exit_ts=end_ts,
                exit_price=100.0 * (1 + 0.05 if is_win else 1 - 0.03),
                exit_reason="TARGET",
                gross_return=net + 0.002,
                fees=0.002,
                slippage=0.0,
                net_return=net,
                holding_bars=10,
                max_favorable_excursion=0.05,
                max_adverse_excursion=-0.03,
                overlap=False,
            )
        )
    return trades


def _metrics_with(n, win_rate):
    me = MetricsEngine()
    trades = _make_trades(n, win_rate)
    return me.compute(trades, [], 100)


def test_fixed_score_buckets_pre_registered():
    assert [b[0] for b in FIXED_SCORE_BUCKETS] == ["0-19", "20-39", "40-59", "60-79", "80-100"]
    assert all(b[1] <= b[2] for b in FIXED_SCORE_BUCKETS)


def test_validator_runs_single_dataset_and_produces_all_sections(settings):
    v = Phase5Validator(settings=settings)
    df = _frame()
    res = v.validate("TEST/USDT", "1D", df=df)
    assert res.symbol == "TEST/USDT"
    assert res.candle_count == len(df)
    assert res.strategy_version
    assert res.overall.trades.total_setups >= 0
    labels = [p.label for p in res.periods]
    assert labels == ["IN", "VALIDATION", "OOS"]
    idxs = [p.index.start for p in res.periods]
    assert idxs[0] < idxs[1] < idxs[2]
    d = res.to_dict()
    assert d["overall"]
    assert len(d["periods"]) == 3


def test_validator_periods_disjoint_cover_input(settings):
    v = Phase5Validator(settings=settings)
    df = _frame(n=300)
    res = v.validate("TEST/USDT", "1D", df=df)
    spans = [(p.index.start, p.index.stop) for p in res.periods]
    assert spans[0][0] == 0
    assert spans[-1][1] == len(df)
    for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
        assert a1 <= b0


def test_calibration_reports_discrimination_or_none(settings):
    v = Phase5Validator(settings=settings)
    df = _frame(n=120, seed=99)
    res = v.validate("TEST/USDT", "1D", df=df)
    cal = CalibrationEngine().assess(res.overall)
    assert cal.baseline_win_rate == 0.5
    assert cal.score_is_useful in (True, False, None)
    assert cal.monotonic_win_rate in (True, False, None)


def test_calibration_win_ci_wilson():
    from veyra.phase5.calibration import _wilson

    lo, hi = _wilson(50, 100)
    assert lo is not None and hi is not None
    assert lo <= 0.5 <= hi
    assert _wilson(0, 0) == (None, None)


def test_gates_insufficient_without_evidence():
    gates = Phase5Gates()
    bd = Build(dataset_count=0, datasets_validated=0)
    ev = Evidence(oos=None, overall=None, calibration=None)
    card = gates.evaluate(bd, ev)
    assert card.verdict == Verdict.INSUFFICIENT_EVIDENCE
    assert len(card.gates) == 7


def test_gates_supported_when_all_clean():
    gates = Phase5Gates()
    bd = Build(
        dataset_count=2,
        datasets_validated=2,
        slice_progressive_equivalent=True,
        freeze_test_passed=True,
        baseline_frozen=True,
    )
    ev = Evidence(
        oos=_metrics_with(40, 0.55),
        overall=_metrics_with(40, 0.55),
        calibration=CalibrationResult(score_is_useful=True, kendall_win_rate=1.0),
    )
    card = gates.evaluate(bd, ev)
    assert card.verdict == Verdict.SUPPORTED


def test_gates_score_failure_surfaces_honestly():
    gates = Phase5Gates()
    bd = Build(
        dataset_count=2,
        datasets_validated=2,
        slice_progressive_equivalent=True,
        freeze_test_passed=True,
        baseline_frozen=True,
    )
    ev = Evidence(
        oos=_metrics_with(40, 0.55),
        overall=_metrics_with(40, 0.55),
        calibration=CalibrationResult(score_is_useful=False, notes=["no edge"]),
    )
    card = gates.evaluate(bd, ev)
    statuses = {g.gate: g.status for g in card.gates}
    assert statuses["G"] == GateStatus.FAIL
    assert card.verdict in (Verdict.MIXED, Verdict.WEAK)


def test_gates_weak_when_negative_edge_but_stable():
    gates = Phase5Gates()
    bd = Build(dataset_count=2, datasets_validated=2,
               slice_progressive_equivalent=True, freeze_test_passed=True,
               baseline_frozen=True)
    # OOS: enough trades, stable win-rate, but negative average return -> WEAK.
    ev = Evidence(
        oos=_metrics_negative_edge(40),
        overall=_metrics_negative_edge(40),
        calibration=None,
    )
    card = gates.evaluate(bd, ev)
    d_gate = next(g for g in card.gates if g.gate == "D")
    assert d_gate.status == GateStatus.WEAK


def _metrics_negative_edge(n):
    me = MetricsEngine()
    return me.compute(_make_trades(n, 0.4), [], 100)


def test_gates_data_provenance_fail():
    gates = Phase5Gates()
    bd = Build(dataset_count=4, datasets_validated=1)
    ev = Evidence(oos=None, overall=None, calibration=None)
    card = gates.evaluate(bd, ev)
    a_gate = card.gates[0]
    assert a_gate.gate == "A"
    assert a_gate.status == GateStatus.FAIL


def test_report_answers_all_14_questions(settings, tmp_path):
    from veyra.phase5 import (
        Phase5ReportSuite,
        ReportInputs,
    )

    v = Phase5Validator(settings=settings)
    df = _frame(n=240, seed=42)
    res = v.validate("TEST/USDT", "1D", df=df)
    cal = CalibrationEngine().assess(res.overall)
    bd = Build(dataset_count=1, datasets_validated=1,
               slice_progressive_equivalent=True, freeze_test_passed=True,
               baseline_frozen=True)
    card = Phase5Gates().evaluate(
        bd,
        Evidence(oos=res.periods[2].metrics, overall=res.overall, calibration=cal),
    )
    out = Phase5ReportSuite(tmp_path).write_dataset(
        ReportInputs(
            symbol=res.symbol,
            timeframe=res.timeframe,
            validation=res,
            calibration=cal,
            card=card,
            provenance={"provider": "binance"},
            data_quality={"count": res.candle_count, "gaps": 0,
                          "duplicates": 0, "reversed": 0},
        )
    )
    assert out.exists()
    md = out.read_text()
    assert md.count("## Q") == 14
    assert "No profitability claim" in md
    assert "Gates A" in md
    json_path = tmp_path / (out.name.replace(".md", ".json"))
    assert json_path.exists()


def test_robustness_respects_cost_scenarios():
    from veyra.phase5 import RobustnessEngine

    trades = _make_trades(40, 0.6)
    rob = RobustnessEngine().run(trades)
    # Average return must fall as costs rise and match zero-cost = gross.
    avgs = [s.avg_return for s in rob.scenarios]
    assert avgs[0] >= avgs[1]  # base >= cost_x2
    assert avgs[-1] >= avgs[0]  # cost_zero >= base
    assert rob.base.net_trades == len(trades)
    assert rob.base.fees_pct > 0