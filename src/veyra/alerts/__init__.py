"""Selective setup alerts for Veyra.

A setup alert is a machine-generated *notice* about a qualified, high-score
setup. Alerts are SIMULATION ONLY — they never place orders, touch real money,
or contact an exchange. They exist so a human can selectively review the most
defensible opportunities surfaced by the frozen pipeline.

Public API:
  * AlertPolicy       — selectivity rules (min score, cap, qualifying states)
  * AlertLevel        — clarity band derived from the alignment score
    (CONVERGENT / DIRECTIONAL / EMERGENT / SCANNED)
  * SetupAlert        — one alert record (serializable / loadable)
  * AlertProcessor    — converts a replay result into alerts
  * AlertService      — compute/cache alert bundles across datasets
  * LiveScan          — fast "live" scan over the latest candle tail (no replay)
"""

from __future__ import annotations

from . import live, models, policy, service
from .live import LiveScan
from .models import AlertLevel, SetupAlert
from .policy import AlertPolicy
from .processor import AlertProcessor
from .service import AlertService

__all__ = [
    "AlertLevel",
    "AlertPolicy",
    "AlertProcessor",
    "AlertService",
    "LiveScan",
    "SetupAlert",
    "live",
    "models",
    "policy",
    "service",
]

# A guard mirroring the paper trader's: alerts are notices, never trades.
REAL_EXECUTION_FORBIDDEN = "setup alerts are simulation-only; no execution"