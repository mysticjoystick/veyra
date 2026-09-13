"""Phase 4 split + walk-forward validation tests.

Time-series data is never shuffled. Splits are chronological, non-overlapping,
and the test period is isolated to forbid tuning on it.
"""

from __future__ import annotations

import pytest

from veyra.backtest.split import (
    Split,
    chronological_split,
    walk_forward_windows,
)
from veyra.backtest.models import SplitConfig
from veyra.backtest.validation import (
    TestSetLeakError as _TestSetLeakError,
    assert_not_test,
    extend_start,
    report_split_boundaries,
)


def test_split_is_chronological_and_exhaustive():
    ind = chronological_split(1000)
    splits = ind.as_list()
    assert splits[0].start == 0
    assert splits[-1].end == 1000
    for a, b in zip(splits, splits[1:]):
        assert a.end == b.start  # contiguous, no gap, no overlap
    assert splits[0].name == "training"
    assert splits[2].name == "test"


def test_split_respects_fractions():
    ind = chronological_split(1000, SplitConfig(0.6, 0.2, 0.2))
    assert ind.training.end == 600
    assert ind.validation.end == 800
    assert ind.test.end == 1000


def test_split_rejects_fractions_summing_over_one():
    with pytest.raises(ValueError):
        chronological_split(100, SplitConfig(0.6, 0.3, 0.3))


def test_leak_guard_blocks_test_candles_from_analysis():
    test = Split("test", 600, 800)
    with pytest.raises(_TestSetLeakError):
        assert_not_test(index=700, test=test)


def test_assert_not_test_ok_inside_train_or_val():
    test = Split("test", 600, 800)
    assert_not_test(index=550, test=test)  # in training -> ok
    assert_not_test(index=590, test=test)  # just before test -> ok
    assert_not_test(index=800, test=test)  # at boundary (exclusive) -> ok


def test_extend_start_never_starts_after_end():
    assert extend_start(0, 10) == 0
    assert extend_start(5, 10) == 5
    assert extend_start(12, 10) == 10  # clamp


def test_walk_forward_windows_advance_and_do_not_leak():
    w = walk_forward_windows(1200, 400, 200, 200, 200)
    assert len(w) == 3  # 0-800, 200-1000, 400-1200
    # Test windows are strictly non-overlapping and advance monotonically.
    tests = [x.test_start for x in w]
    assert tests == sorted(tests)
    # Each window: train ends where validation begins, etc.
    for x in w:
        assert x.validation_start == x.train_end
        assert x.test_start == x.validation_end


def test_walk_forward_stops_when_dataset_short():
    assert walk_forward_windows(100, 400, 200, 200, 200) == []


def test_report_split_boundaries_returns_metadata():
    ind = chronological_split(1000)
    report = report_split_boundaries(ind)
    assert set(report) == {"training", "validation", "test"}
    assert report["test"]["start"] >= 600  # test is isolated at the end