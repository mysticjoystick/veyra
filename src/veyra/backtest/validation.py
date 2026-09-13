"""Out-of-sample discipline utilities.

Makes it harder to accidentally inspect or fit on the test period. These helpers
are lightweight guards: they do not prevent a determined user from cheating, but
they surface the intended boundaries and refuse clear misuse (e.g. reporting
test-period fitting metadata as validation).
"""

from __future__ import annotations

from typing import List

from .split import Split


class TestSetLeakError(Exception):
    """Raised when a validation/trusted operation targets the test period."""


def extend_start(candle_count: int, warmup_bars: int) -> int:
    """First candle index at which analysis can begin (after warm-up).

    Early candles form an unavoidable warm-up for EMA200/MACD; they are part of
    the simulated history but produce no setups. This is not a leak — it only
    moves the *start* of trustworthy detection later.
    """
    return min(candle_count, warmup_bars)


def assert_not_test(index: int, test: Split) -> None:
    """Raise if a candle index falls within the designated test window."""
    if test.start <= index < test.end:
        raise TestSetLeakError(
            f"Index {index} lies in the reserved test period "
            f"[{test.start}, {test.end}). Treating it as train/validation "
            "biases the results."
        )


def report_split_boundaries(splits) -> dict:
    """Summarise split boundaries for provenance (never used for fitting)."""
    return {
        s.name: {
            "start": s.start,
            "end": s.end,
            "size": s.end - s.start,
        }
        for s in splits.as_list()
    }