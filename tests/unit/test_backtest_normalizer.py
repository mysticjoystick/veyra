"""Score scale regression tests.

Scoring now produces one canonical 0-100 score directly: the aggregator
returns the weighted average of component alignment, so live snapshot, setups,
backtest records, alerts and paper trading all share the same number and the
same clarity ladder. ScoreNormalizer remains as an identity/clamping adapter
so no consumer double-scales.
"""

from __future__ import annotations

import pytest

from veyra.backtest import ScoreNormalizer
from veyra.backtest.normalize import default_weight_sum
from veyra.strategy.aggregation import WeightedScoreAggregator


def _aggregator() -> WeightedScoreAggregator:
    return WeightedScoreAggregator(
        weights={
            "trend": 0.25,
            "structure": 0.20,
            "pullback": 0.15,
            "momentum": 0.12,
            "volume": 0.07,
            "volatility": 0.05,
        }
    )


def test_default_weight_sum_matches_aggregator():
    assert default_weight_sum() == pytest.approx(_aggregator().weight_sum)


def test_normalizer_is_passthrough_on_canonical_scale():
    ws = _aggregator().weight_sum
    n = ScoreNormalizer(weight_sum=ws)
    # The canonical score is already 0-100; the adapter must not rescale.
    assert n.normalized(0) == 0
    assert n.normalized(50) == 50
    assert n.normalized(100) == 100
    assert n.raw_max == 100


def test_normalizer_never_exceeds_100():
    n = ScoreNormalizer(weight_sum=_aggregator().weight_sum)
    assert n.normalized(101) == 100
    assert n.normalized(-5) == 0


def test_normalizer_rejects_zero_weight_sum():
    with pytest.raises(ValueError):
        ScoreNormalizer(weight_sum=0.0)


def test_normalizer_backed_by_engine_aggregator_roundtrip():
    """A real BacktestEngine must expose a normalizer consistent with the live
    aggregator so reported scores share one scale."""
    from veyra.backtest import BacktestEngine
    from veyra.config import Settings

    engine = BacktestEngine(Settings())
    assert engine._normalizer.weight_sum == pytest.approx(_aggregator().weight_sum)


def test_live_setup_score_is_canonical_0_100():
    """A real setup's overall score and score_normalized must be the SAME
    canonical 0-100 value (never raw 0-84, never double-scaled)."""
    from veyra.backtest import BacktestEngine
    from veyra.config import Settings
    from tests.unit.market_synth import bull_continuation_frame

    engine = BacktestEngine(Settings())
    res = engine.run("BTC/USDT", "4H", bull_continuation_frame(n=300, start_ts=1_000_000_000))
    for s in res.setups:
        assert 0 <= s.score <= 100
        assert 0 <= s.score_normalized <= 100
        # One canonical number everywhere.
        assert s.score_normalized == s.score