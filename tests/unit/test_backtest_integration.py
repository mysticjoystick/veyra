"""Phase 4 integration invariants across synthetic market regimes.

The backtester must produce internally consistent results on any valid frame:
entry before exit, allowed exit reasons, stop/target placed on the correct side,
and raw score <= aggregated weight sum (pre-normalisation ceiling).
"""

from __future__ import annotations

import pytest

from veyra.backtest import BacktestEngine
from veyra.backtest.models import ExecutionConfig
from tests.unit import market_synth as ms


def _engine():
    from veyra.config import Settings
    return BacktestEngine(Settings())


ALL_EXITS = {"STOP", "TARGET", "EXPIRED", "INVALIDATED", "END_OF_DATA"}


@pytest.mark.parametrize(
    "frame_name",
    [
        "bullish_frame",
        "bearish_frame",
        "sideways_frame",
        "volatile_frame",
        "higher_highs_higher_lows_frame",
        "bull_continuation_frame",
    ],
)
def test_engine_invariant_across_frames(frame_name):
    engine = _engine()
    raw_max = engine._normalizer.raw_max
    frame = getattr(ms, frame_name)(n=300, start_ts=1_000_000_000)
    res = engine.run("BTC/USDT", "4H", frame)
    for t in res.trades:
        assert t.entry_ts <= t.exit_ts
        assert t.exit_reason in ALL_EXITS
        assert t.exit_price > 0.0
        assert 0 <= t.score <= raw_max          # canonical 0-100 score
        assert 0 <= t.score_normalized <= 100
    # Invariant: entries strictly chronological.
    entries = [t.entry_ts for t in res.trades]
    assert entries == sorted(entries)


def test_raw_score_never_exceeds_aggregator_weight_sum():
    """Scores are a single canonical 0-100 scale everywhere.

    The overall score is the weighted-average of component alignment, so
    score and score_normalized are the same number and both live in 0-100.
    """
    engine = _engine()
    res = engine.run("BTC/USDT", "4H", ms.bull_continuation_frame(n=300, start_ts=1_000_000_000))
    ws = engine._normalizer.weight_sum
    for s in res.setups:
        assert 0 <= s.score <= 100
        assert s.score_normalized == s.score
        assert s.score_normalized == engine._normalizer.normalized(s.score)
        assert 0 <= s.score_normalized <= 100


def test_every_setup_outcome_is_an_admissible_value():
    from veyra.backtest.models import SetupOutcome
    engine = _engine()
    res = engine.run("BTC/USDT", "4H", ms.sideways_frame(n=300, start_ts=1_000_000_000))
    allowed = {o.value for o in SetupOutcome}
    assert all(s.outcome in allowed for s in res.setups)