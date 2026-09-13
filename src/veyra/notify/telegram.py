"""Send-safe Telegram transport (stdlib only).

The default is DRY-RUN: with no bot token or chat id configured (or with
dry_run enabled), the notifier writes the outgoing message to the log level
specified and returns without ever touching the network. No token, no send.

When a token AND a chat id are provided AND dry_run is False, it POSTs to the
Telegram sendMessage API using urllib. It is deliberately dependency-free and
deliberately constrained to text-only sendMessage — no media, no edits, no
chat administration. A failure to send is caught and surfaced (never raised
uncaught) so an operator can resend later.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger("veyra.notify")

SENDMESSAGE_URL = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramNotifier:
    """Send a single text message to a Telegram chat.

    Safe by default: if dry_run is True, or the bot token / chat id is empty,
    the message is logged (level `dry_log_level`) and not transmitted.
    """

    def __init__(
        self,
        bot_token: str = "",
        chat_id: str = "",
        dry_run: bool = True,
        dry_log_level: int = logging.INFO,
        require_entitlement: bool = False,
        entitled: bool = True,
    ) -> None:
        self._bot_token = bot_token or ""
        self._chat_id = str(chat_id or "")
        self._dry_run = bool(dry_run)
        self._dry_log_level = dry_log_level
        # Phase 9: when require_entitlement is on, a non-paying account is never
        # contacted — send() returns a "gated" record and no transport happens.
        self._require_entitlement = bool(require_entitlement)
        self._entitled = bool(entitled) if self._require_entitlement else True

    @property
    def configured(self) -> bool:
        """True only when a real send is possible (token + chat + not dry-run)."""
        return (
            not self._dry_run
            and bool(self._bot_token)
            and bool(self._chat_id)
            and self._entitled
        )

    @property
    def gated(self) -> bool:
        """True when entitlement is required but the account is not paying."""
        return self._require_entitlement and not self._entitled

    def send(self, text: str) -> dict:
        """Deliver (or dry-log) a message, returning a small status dict.

        Returns a record the caller can persist/audit:
          {"sent": bool, "dry_run": bool, "chat_id": ..., "error": None|str}

        A non-paying account under an entitlement requirement is never
        contacted: it returns a "gated" record with no transport (no network,
        no dry-log of a would-be live send).
        """
        text = str(text or "").strip()
        if not text:
            return {
                "sent": False,
                "dry_run": self._dry_run,
                "chat_id": self._chat_id,
                "error": "empty message",
                "gated": False,
            }

        if self.gated:
            logger.warning(
                "[gated] TG delivery skipped: requires paid subscription (chat %s)",
                self._chat_id or "(unset)",
            )
            return {
                "sent": False,
                "dry_run": self._dry_run,
                "chat_id": self._chat_id,
                "error": "requires paid subscription",
                "gated": True,
            }

        if self._dry_run or not self._bot_token or not self._chat_id:
            logger.log(self._dry_log_level, "[dry-run] TG -> %s: %s", self._chat_id or "(unset)", text)
            return {
                "sent": False,
                "dry_run": True,
                "chat_id": self._chat_id,
                "error": "dry-run: not transmitted",
                "gated": False,
            }

        try:
            url = SENDMESSAGE_URL.format(token=self._bot_token)
            payload = json.dumps(
                {
                    "chat_id": self._chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                }
            ).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "veyra/notify"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - outbound HTTPS post
                body = resp.read().decode("utf-8", "replace")
            ok = bool(json.loads(body).get("ok"))
            logger.log(self._dry_log_level, "TG sent -> %s (ok=%s)", self._chat_id, ok)
            return {"sent": ok, "dry_run": False, "chat_id": self._chat_id, "error": None, "gated": False}
        except (urllib.error.URLError, ValueError, OSError) as exc:  # noqa: BLE001
            logger.warning("TG send failed: %s", exc)
            return {"sent": False, "dry_run": False, "chat_id": self._chat_id, "error": str(exc), "gated": False}