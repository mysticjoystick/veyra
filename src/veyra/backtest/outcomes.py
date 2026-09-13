"""Outcome classification for setups.

The backtester must preserve the distinction between a *detected* setup and a
*traded* outcome. A setup that is detected but never qualifies, or qualifies
but never triggers, is NOT a losing trade. This module assigns every setup a
SetupOutcome based on its lifecycle terminal state and whether a trade resulted.
"""

from __future__ import annotations

from ..domain import SetupState
from .models import SetupOutcome


def classify_outcome(final_state: str, trade_id=None) -> SetupOutcome:
    """Map a setup's final state + optional trade to a SetupOutcome.

    A setup that produced a TRIGGERED trade is treated as COMPLETED (it traded);
    its exit reason is captured on the trade, not conflated with the setup's own
    lifecycle expiry/invalidation. Setups that never triggered are classified
    from their terminal state so they are NOT counted as trades.
    """
    if trade_id is not None:
        return SetupOutcome.COMPLETED
    if final_state in (SetupState.INVALIDATED.value,):
        return SetupOutcome.INVALIDATED
    if final_state in (SetupState.EXPIRED.value,):
        return SetupOutcome.EXPIRED
    if final_state == SetupState.QUALIFIED.value:
        return SetupOutcome.QUALIFIED_NO_TRADE
    if final_state in (SetupState.DETECTED.value, SetupState.DEVELOPING.value):
        return SetupOutcome.DETECTED_ONLY
    return SetupOutcome.QUALIFIED_NO_TRADE