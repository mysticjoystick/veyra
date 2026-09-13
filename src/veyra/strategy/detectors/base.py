"""Setup detector base contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from ..candidate import SetupCandidate
from ..snapshot_view import SnapshotView


class SetupDetector(ABC):
    """Detects zero or one candidate of a single setup type from a snapshot.

    Each detector is a pure rule set over the Phase 2 snapshot: no indicator
    recalculation, no randomness, no external calls. A separate engine asks
    every detector in turn and merges results.
    """

    #: The AnalyticsComponent-scale cap each detector relies on. Kept explicit
    #: so scoring can be understood without reading the detector body.
    name: str = "detector"

    @abstractmethod
    def detect(self, view: SnapshotView) -> Optional[SetupCandidate]:
        """Return a candidate if rules fire, else None."""
        raise NotImplementedError