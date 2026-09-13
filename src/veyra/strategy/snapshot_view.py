"""Shared snapshot-view helpers for detectors.

Detectors consume the Phase 2 MarketSnapshot rather than recomputing
indicators. These helpers give access to each component's structured meta in a
stable, tolerant way (missing/in-sufficient data is returned as UNKNOWN-ish
sentinel values rather than raising).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ..domain.snapshot import MarketSnapshot


class SnapshotView:
    """Immutable, tolerant reader over a MarketSnapshot."""

    def __init__(self, snapshot: MarketSnapshot) -> None:
        self._snapshot = snapshot

    @property
    def symbol(self) -> str:
        return self._snapshot.symbol

    @property
    def timeframe(self) -> str:
        return self._snapshot.timeframe

    @property
    def timestamp(self) -> int:
        return self._snapshot.timestamp

    @property
    def regime(self) -> str:
        return self._snapshot.regime.value

    @property
    def data_quality_state(self) -> str:
        return self._snapshot.data_quality.resolve().value

    def meta(self, component: str) -> Dict[str, Any]:
        comp = self._snapshot.components.get(component)
        return dict(comp.meta) if comp else {}

    def score(self, component: str) -> int:
        """The engine's genuine aligned 0-100 score for a component.

        This is the alignment score produced by the market engines and placed in
        ``AnalysisComponentOutput.score`` (NOT in ``meta``). Returns 0 when the
        component is absent or not ready so callers stay tolerant.
        """
        comp = self._snapshot.components.get(component)
        if comp is None:
            return 0
        return max(0, min(100, int(comp.score)))

    def price(self) -> Optional[float]:
        """Current price (last close) from the structure component's value.

        The structure engine's EngineResult.value is the latest close; fall
        back to None if unavailable so detectors never fabricate a price.
        """
        comp = self._snapshot.components.get("STRUCTURE")
        value = (comp.value if comp else None)
        if value is None:
            value = self.meta("STRUCTURE").get("value")
        return float(value) if value is not None else None

    def is_ready(self, component: str) -> bool:
        comp = self._snapshot.components.get(component)
        return bool(comp and comp.state == "READY")


def component_meta(snapshot: MarketSnapshot, component: str) -> Dict[str, Any]:
    return SnapshotView(snapshot).meta(component)