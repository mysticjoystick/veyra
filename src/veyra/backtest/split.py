"""Chronological dataset splits and walk-forward windows.

Time-series data must NEVER be randomly shuffled. Splits are always
chronological and non-overlapping. The test window is designated up-front so a
researcher is discouraged from tuning on it.

Walk-forward sweeps a train -> validation -> test window across the dataset in
chronological steps, producing independent test windows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .models import SplitConfig


@dataclass(frozen=True)
class Split:
    """Index ranges (inclusive of endpoints within a half-open [start,end))."""

    name: str                      # "training" | "validation" | "test"
    start: int                     # inclusive candle index
    end: int                       # exclusive candle index


@dataclass(frozen=True)
class SplitIndices:
    training: Split
    validation: Split
    test: Split

    def as_list(self) -> List[Split]:
        return [self.training, self.validation, self.test]

    def to_dict(self) -> dict:
        return {s.name: (s.start, s.end) for s in self.as_list()}


def chronological_split(
    n_candles: int,
    config: SplitConfig = SplitConfig(),
) -> SplitIndices:
    """Compute a deterministic chronological train/validation/test split.

    Percentages are applied to the candle count; boundaries are rounded down so
    the split always fits within the dataset. Any remainder (due to rounding) is
    absorbed into the trailing (test) partition, preserving chronological order.
    """
    if n_candles < 1:
        raise ValueError("n_candles must be >= 1")

    t = config.train
    v = config.validation
    test = config.test
    total = t + v + test
    if total > 1.0 + 1e-9:
        raise ValueError("Split fractions must sum to <= 1.0")

    train_end = int(round(n_candles * t))
    val_end = train_end + int(round(n_candles * v))
    # Test absorbs any remainder.
    test_end = n_candles

    return SplitIndices(
        training=Split("training", 0, train_end),
        validation=Split("validation", train_end, val_end),
        test=Split("test", val_end, test_end),
    )


@dataclass(frozen=True)
class WalkForwardWindow:
    """One walk-forward replicate: train window that produced a test window."""

    index: int
    train_start: int
    train_end: int
    validation_start: int
    validation_end: int
    test_start: int
    test_end: int

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "train": (self.train_start, self.train_end),
            "validation": (self.validation_start, self.validation_end),
            "test": (self.test_start, self.test_end),
        }


def walk_forward_windows(
    n_candles: int,
    train_bars: int,
    validation_bars: int,
    test_bars: int,
    step_bars: int,
) -> List[WalkForwardWindow]:
    """Produce chronological walk-forward windows.

    Each window has a training block, an immediately-following validation block,
    and a following test block. Windows advance by `step_bars` along the
    timeline. The last window may be truncated if the dataset ends; windows never
    overlap in their test portions unless the dataset is too short for the
    configured geometry (in which case fewer windows are produced).
    """
    if n_candles < 1:
        return []
    total_span = train_bars + validation_bars + test_bars
    windows: List[WalkForwardWindow] = []
    idx = 0
    pos = 0
    while pos + total_span <= n_candles:
        windows.append(
            WalkForwardWindow(
                index=idx,
                train_start=pos,
                train_end=pos + train_bars,
                validation_start=pos + train_bars,
                validation_end=pos + train_bars + validation_bars,
                test_start=pos + train_bars + validation_bars,
                test_end=pos + total_span,
            )
        )
        idx += 1
        pos += step_bars
    return windows