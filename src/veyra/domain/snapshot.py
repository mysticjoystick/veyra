"""Market snapshot models.

A MarketSnapshot captures the full analytical state of a market at a point
in time: per-component analysis (trend, structure, momentum, volume,
volatility) with their structured evidence, the resolved regime, data
quality, component scores, and system state.

It is serializable and carries enough information to reproduce/explain the
analysis without the UI recomputing indicators. It is independent of UI and
storage layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from . import (
    AnalyticsComponent,
    Regime,
    SystemState,
)
from .data_quality import DataQuality


@dataclass(frozen=True)
class ComponentScore:
    component: AnalyticsComponent
    score: int  # 0-100
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "component": self.component.value,
            "score": self.score,
            "detail": self.detail,
        }


@dataclass
class AnalysisComponentOutput:
    """Captures one engine's structured output + evidence."""

    name: str                       # AnalyticsComponent value (e.g. "TREND")
    state: str = "UNKNOWN"
    score: int = 0
    value: Optional[float] = None
    detail: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "score": self.score,
            "value": self.value,
            "detail": self.detail,
            "meta": self.meta,
            "evidence": self.evidence,
        }


@dataclass
class RegimeOutput:
    regime: Regime = Regime.UNKNOWN
    score: int = 0
    evidence: Dict[str, Any] = field(default_factory=dict)
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "regime": self.regime.value,
            "score": self.score,
            "evidence": self.evidence,
            "detail": self.detail,
        }


@dataclass
class MarketSnapshot:
    symbol: str
    timeframe: str
    timestamp: int  # epoch seconds, UTC
    regime: Regime = Regime.UNKNOWN
    scores: Dict[AnalyticsComponent, int] = field(default_factory=dict)
    overall_score: int = 0
    system_state: SystemState = SystemState.WAIT
    components: Dict[str, AnalysisComponentOutput] = field(default_factory=dict)
    regime_output: RegimeOutput = field(default_factory=RegimeOutput)
    data_quality: DataQuality = field(default_factory=DataQuality)

    def component(self, name: AnalyticsComponent) -> Optional[int]:
        return self.scores.get(name)

    def set_component(self, name: AnalyticsComponent, score: int) -> None:
        self.scores[name] = max(0, min(100, score))

    def add_component_output(self, output: AnalysisComponentOutput) -> None:
        self.components[output.name] = output

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "timestamp": self.timestamp,
            "regime": self.regime.value,
            "regime_output": self.regime_output.to_dict(),
            "scores": {k.value: v for k, v in self.scores.items()},
            "overall_score": self.overall_score,
            "system_state": self.system_state.value,
            "components": {
                k: v.to_dict() for k, v in self.components.items()
            },
            "data_quality": self.data_quality.to_dict(),
        }