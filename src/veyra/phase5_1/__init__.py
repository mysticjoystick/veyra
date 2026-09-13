"""Veyra Phase 5.1 — strategy diagnosis (frozen baseline analysis).

Phase 5.1 diagnoses WHERE and WHY the frozen Phase 5 baseline under-performs,
without changing it. It re-uses the Phase 5 validation infrastructure and the
immutable real datasets. No optimisation, no ML, no live trading, no cherry
picking. Every conclusion carries its sample size.
"""

from .diagnostics import (
    SETUP_TYPES,
    CONFIGURED_REGIMES,
    TIME_FRAMES,
    SCORE_BUCKETS,
    DEFAULT_DATASETS_SPEC,
    DatasetData,
    DiagnosticsSet,
    SliceStats,
    count_outcomes,
    slice_stats,
    validate_dataset,
)

__all__ = [
    "SETUP_TYPES",
    "CONFIGURED_REGIMES",
    "TIME_FRAMES",
    "SCORE_BUCKETS",
    "DEFAULT_DATASETS_SPEC",
    "DatasetData",
    "DiagnosticsSet",
    "SliceStats",
    "count_outcomes",
    "slice_stats",
    "validate_dataset",
]