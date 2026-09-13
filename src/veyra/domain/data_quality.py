"""Data-quality state model.

Carries Phase 1 data-quality information (validation result, gaps,
insufficient history) into the analysis layer so the pipeline does not
pretend unreliable data is fully trustworthy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from . import DataQualityState


@dataclass
class DataQuality:
    state: DataQualityState = DataQualityState.UNKNOWN
    row_count: int = 0
    required_lookback: int = 0
    gap_count: int = 0
    validation_valid: bool = True
    issues: List[dict] = field(default_factory=list)

    @property
    def is_insufficient(self) -> bool:
        return self.row_count < self.required_lookback

    @property
    def has_gaps(self) -> bool:
        return self.gap_count > 0

    def resolve(self) -> DataQualityState:
        """Compute the effective data-quality state from raw signals."""
        if not self.validation_valid:
            return DataQualityState.INVALID
        if self.is_insufficient:
            return DataQualityState.INSUFFICIENT_DATA
        if self.has_gaps:
            return DataQualityState.VALID_WITH_GAPS
        return DataQualityState.VALID

    def to_dict(self) -> dict:
        return {
            "state": self.resolve().value,
            "row_count": self.row_count,
            "required_lookback": self.required_lookback,
            "gap_count": self.gap_count,
            "validation_valid": self.validation_valid,
            "issues": self.issues[:20],
        }