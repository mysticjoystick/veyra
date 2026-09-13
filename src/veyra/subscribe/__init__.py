"""Veyra subscriptions package (Phase 9).

Models per-account subscription entitlements so paid features (notably Telegram
alert delivery) can be gated before a billing processor exists. Phase 10 wires
actual payment; until then premium is provisioned manually for evaluation.
"""

from __future__ import annotations

from .entitlements import (
    EntitlementError,
    PLAN_FREE,
    PLAN_PREMIUM,
    PREMIUM_TTL_DAYS,
    SubscriptionService,
)

__all__ = [
    "SubscriptionService",
    "EntitlementError",
    "PLAN_FREE",
    "PLAN_PREMIUM",
    "PREMIUM_TTL_DAYS",
]