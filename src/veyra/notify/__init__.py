"""Phase 7 — notifications for Veyra.

Delivering selective setup alerts to a human is a *notification* concern, never
an execution one. This package sends nothing on its own; it converts alert
state into a message and hands it to a transport.

Honesty rules enforced here:
  * Sending is opt-in. The default is DRY-RUN: a message is rendered/logged
    and the network is never touched.
  * A real send happens only when a bot token AND a chat id are configured AND
    dry-run is explicitly disabled.
  * The clerk only reports state *changes* (DETECTED -> DEVELOPING -> QUALIFIED
    -> TRIGGERED / INVALIDATED / EXPIRED / COMPLETED), and never invents a
    message for an unchanged alert.

Public API:
  * TelegramNotifier  - safe stdlib-only transport (urllib), dry-run by default
  * NotifyClerk        - idempotent state-change notifier persisted to disk
"""

from __future__ import annotations

from .clerk import NotifyClerk, render_message, setup_state_for
from .telegram import TelegramNotifier
from .stars import StarsBiller

__all__ = [
    "NotifyClerk",
    "TelegramNotifier",
    "StarsBiller",
    "render_message",
    "setup_state_for",
]