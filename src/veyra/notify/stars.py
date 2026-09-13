"""Telegram Stars merchant integration (Phase 10 billing).

Implements a minimal, dependency-free Telegram Bot API client for selling a
monthly premium subscription paid in Telegram Stars (currency ``XTR``). It is
used in two cooperating halves:

 * ``StarsBiller.create_monthly_link(...)``   -> ``createInvoiceLink`` to hand a
   pay button / deep link to the subscriber.
 * ``StarsBiller.poll_once(...)``             -> a single ``getUpdates`` pass
   handling, in one iteration:
       + a plain message whose text is a Veyra account email  -> link/rebind
         the Telegram user_id to that account (proof of ownership),
       + ``pre_checkout_query``                                 -> approve our
         invoices (and CANCELL for anything not ours),
       + ``successful_payment``                                 -> provision
         premium on the linked account and confirm the tier.

Renewal revoke is NOT handled here: a missed renewal simply does not arrive.
The operator runs ``subscription --stars-reconcile`` (or the periodic loop) to
downgrade lapsed subscriptions by checking ``subscription_expires_at``.

The client never logs the bot token and never sends fiat. All failures are
surfaced as plain dicts rather than uncaught exceptions so an operator can
reschedule a run.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

logger = logging.getLogger("veyra.stars")

API_BASE = "https://api.telegram.org/bot{token}/{method}"
EMAIL_PREFIX = "email:"  # how a plain email is tagged so we can ignore links


class TelegramApiError(Exception):
    """Raised when the Bot API returns a non-ok payload."""


class StarsBiller:
    """Thin Bot API client for Stars subscription sales.

    ``on_message(text, telegram_user_id, username)`` and
    ``on_successful_payment(payload, charge_id, telegram_user_id, is_first)``
    are optional callables; when omitted the biller provisions premium itself
    via ``SubscriptionService.grant_by_telegram``.

    Requires a ``subscription_factory`` callable returning a
    ``veyra.subscribe.SubscriptionService`` (or an object with
    ``link_telegram`` / ``find_by_telegram`` / ``grant_by_telegram``).
    """

    def __init__(self, bot_token: str, subscription_factory: Optional[Callable] = None,
                 *, price_xtr: int = 500, period_seconds: int = 30 * 24 * 60 * 60,
                 payload_prefix: str = "veyra:premium:"):
        if not bot_token:
            raise ValueError("bot_token is required for Stars billing")
        self._token = bot_token
        self._subs = subscription_factory
        self._price_xtr = int(price_xtr)
        self._period_seconds = int(period_seconds)
        self._payload_prefix = payload_prefix
        self._offset = 0  # getUpdates offset (long-poll cursor) for <poll_once>

    # ---- low-level Bot API ------------------------------------------------
    def _call(self, method: str, params: dict) -> dict:
        url = API_BASE.format(token=self._token, method=method)
        payload = json.dumps(params).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "veyra/stars"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - HTTPS outbound
            body = resp.read().decode("utf-8", "replace")
        data = json.loads(body)
        if not data.get("ok"):
            raise TelegramApiError(data.get("description") or "telegram api error")
        return data.get("result")

    # ---- invoice tooling ----------------------------------------------------
    def create_monthly_link(self, title: str = "Veyra Premium",
                            description: str = "Monthly access to Veyra premium alerts") -> str:
        """Return a permanent invoice deep link for the monthly premium tier."""
        return self._call(
            "createInvoiceLink",
            {
                "title": title,
                "description": description,
                "payload": self._payload_prefix + "monthly",
                "provider_token": "",                # mandatory for Stars
                "currency": "XTR",
                "prices": [{"label": "1 month", "amount": self._price_xtr}],
                "subscription_period": self._period_seconds,
                "max_tip_amount": 0,
            },
        )

    # ---- update handling -----------------------------------------------------
    def _handle_update(self, update: dict) -> dict:
        result = {"kind": "skip"}
        msg = update.get("message") or {}
        sp = msg.get("successful_payment")
        if sp is not None:
            result = self._handle_successful_payment(sp)
        elif "pre_checkout_query" in update:
            result = self._answer_pre_checkout(update["pre_checkout_query"])
        elif msg:
            result = self._handle_message(msg)
        return result

    def _handle_message(self, msg: dict) -> dict:
        text = (msg.get("text") or "").strip()
        tuser = msg.get("from", {}).get("id")
        username = msg.get("from", {}).get("username")
        if not text or "@" not in text:
            return {"kind": "ignore", "reason": "not an email or unsupported message"}
        if self._subs is None:
            return {"kind": "ignore", "reason": "no subscription service installed"}
        svc = self._subs()
        try:
            user = svc.link_telegram(tuser, text, username=username)
        except Exception as exc:  # noqa: BLE001 - report politely, never crash
            self._say(msg, f"Could not link: {exc}")
            return {"kind": "error", "reason": str(exc)}
        self._say(msg, f"Linked your Telegram to {user.email}. Send the premium pay button / link next to subscribe.")
        return {"kind": "linked", "email": user.email}

    def _say(self, msg: dict, text: str) -> None:
        """Best-effort reply to a chat; never raises."""
        try:
            self._call(
                "sendMessage",
                {"chat_id": msg["chat"]["id"], "text": text, "disable_web_page_preview": True},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("stars reply failed: %s", exc)

    def _answer_pre_checkout(self, q: dict) -> dict:
        payload = q.get("invoice_payload") or ""
        ok = payload.startswith(self._payload_prefix)
        params = {"pre_checkout_query_id": q["id"], "ok": ok}
        if not ok:
            params["error_message"] = "Unrecognized product"
        self._call("answerPreCheckoutQuery", params)
        return {"kind": "pre_checkout", "approved": ok}

    def _handle_successful_payment(self, sp: dict) -> dict:
        payload = sp.get("invoice_payload") or ""
        charge_id = sp.get("telegram_payment_charge_id")
        tuser = sp.get("from", {}).get("id") or sp.get("sender", {}).get("id")
        if not payload.startswith(self._payload_prefix):
            logger.warning("stars: ignoring unrecognized payment payload %r", payload)
            return {"kind": "ignored_payment", "reason": "unrecognized payload"}
        if self._subs is None:
            logger.warning("stars: no subscription service; cannot provision %s", charge_id)
            return {"kind": "unprovisioned", "reason": "no subscription service"}
        is_first = bool(sp.get("is_first_recurring", False))
        svc = self._subs()
        # Extension starts from the receipt's subscription expiry when Telegram
        # tells us; otherwise default to the monthly TTL.
        expires_utc = sp.get("subscription_expiration_date")
        days = None
        if expires_utc:
            from datetime import datetime, timezone

            days = max(0, (datetime.fromtimestamp(expires_utc, tz=timezone.utc) - datetime.now(timezone.utc)).days + 1)
        try:
            user = svc.grant_by_telegram(tuser, days=days)
        except Exception as exc:  # noqa: BLE001
            logger.warning("stars: provision failed for %s: %s", charge_id, exc)
            return {"kind": "unprovisioned", "reason": str(exc)}
        logger.info(
            "stars: provisioned premium for %s (charge=%s, first=%s)",
            user.email, charge_id, is_first,
        )
        return {"kind": "provisioned", "email": user.email, "charge_id": charge_id,
                "is_first": is_first}

    # ---- long-poll loop ------------------------------------------------------
    def poll_once(self, timeout: int = 30) -> dict:
        """Fetch one batch of updates (long-poll) and handle each.

        Returns a small summary dict. The long-poll cursor is kept in ``offset``
        so a caller can repeatedly call ``poll_once`` to stay live.
        """
        params = {"timeout": timeout, "offset": self._offset + 1}
        updates = self._call("getUpdates", params)
        handled: list = []
        for up in updates or []:
            self._offset = max(self._offset, int(up.get("update_id", 0)))
            handled.append(self._handle_update(up))
        return {"updates": len(updates or []), "handled": handled}

    def poll_forever(self, interval: int = 30, stop_after: Optional[int] = None,
                     timeout: int = 30) -> None:
        """Run ``poll_once`` in a loop until interrupted or ``stop_after`` passes."""
        runs = 0
        while stop_after is None or runs < stop_after:
            try:
                summary = self.poll_once(timeout=timeout)
                if summary.get("updates", 0):
                    logger.info("stars: %s", summary)
            except Exception as exc:  # noqa: BLE001
                logger.warning("stars poll failed (will retry): %s", exc)
            runs += 1
            time.sleep(float(interval))