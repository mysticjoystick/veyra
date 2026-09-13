"""Subscription entitlements (Phase 9, billing wired in Phase 10).

Models per-account entitlement state so paid features (notably Telegram alert
delivery) are gated honestly. Payment itself is handled by the Telegram Stars
merchant integration (``veyra.notify.stars``); this module owns the resulting
*entitlement* and the Telegram identity -> account link.

What "paid / premium" means:
  * plan == "premium" on the account AND the subscription has not lapsed
    (subscription_expires_at is None or in the future).

Admin/operator accounts are always entitled so delivery can be verified without
a billing backend. A Telegram Stars payment provisions premium on the linked
account; a lapsed/missed renewal downgrades it (enforced by the merchant loop).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session as OrmSession

from ..database.models import TelegramLink, User

PLAN_FREE = "free"
PLAN_PREMIUM = "premium"
PREMIUM_TTL_DAYS = 30


def utcnow_dt() -> datetime:
    return datetime.now(timezone.utc)


class EntitlementError(Exception):
    """Raised on invalid subscription operations (e.g. unknown user)."""


class SubscriptionService:
    def __init__(self, session_factory) -> None:
        self._factory = session_factory

    # ---- inventory helpers ---------------------------------------------
    def get(self, user_id: int) -> Optional[User]:
        with self._factory() as s:
            return s.query(User).filter_by(id=user_id).first()

    def find_by_email(self, email: str) -> Optional[User]:
        with self._factory() as s:
            return s.query(User).filter_by(email=(email or "").strip().lower()).first()

    # ---- grant / revoke ---------------------------------------------------
    def grant_premium(self, email: str, days: Optional[int] = None) -> User:
        """Mark an account as a paid subscriber.

        With ``days`` set, the subscription lapses after that many days from
        now; with None it is perpetual (test/operator convenience). Raises
        ``EntitlementError`` if the account does not exist.
        """
        email = (email or "").strip().lower()
        with self._factory() as s:
            user = s.query(User).filter_by(email=email).first()
            if user is None:
                raise EntitlementError(f"no account for {email!r}")
            user.plan = PLAN_PREMIUM
            user.subscription_expires_at = (
                utcnow_dt() + timedelta(days=days) if days is not None else None
            )
            s.commit()
            s.refresh(user)
            return user

    def revoke_premium(self, email: str) -> User:
        """Downgrade an account to free and clear its expiry."""
        email = (email or "").strip().lower()
        with self._factory() as s:
            user = s.query(User).filter_by(email=email).first()
            if user is None:
                raise EntitlementError(f"no account for {email!r}")
            user.plan = PLAN_FREE
            user.subscription_expires_at = None
            s.commit()
            s.refresh(user)
            return user

    # ---- Telegram identity link (Phase 10 Stars billing) ------------------
    def link_telegram(self, telegram_user_id, email: str, username: Optional[str] = None) -> User:
        """Bind a Telegram user_id to a Veyra account by email (proof of ownership).

        The subscriber sends their account email to the merchant bot; once the
        email matches a real Veyra account, the binding is stored so subsequent
        Stars payments/renewals are attributed to that account. Raises
        ``EntitlementError`` on an unknown email or a clash with an existing link.
        """
        email = (email or "").strip().lower()
        if not email or "@" not in email:
            raise EntitlementError("a valid email is required to link")
        with self._factory() as s:
            user = s.query(User).filter_by(email=email).first()
            if user is None:
                raise EntitlementError("no account matches that email; register it first")
            existing = (
                s.query(TelegramLink)
                .filter(TelegramLink.telegram_user_id == telegram_user_id)
                .first()
            )
            if existing is not None and existing.user_id != user.id:
                raise EntitlementError("this Telegram identity is linked to another account")
            already = (
                s.query(TelegramLink).filter_by(user_id=user.id).first()
            )
            if already is not None and already.telegram_user_id != telegram_user_id:
                raise EntitlementError("this Veyra account is linked to another Telegram identity")
            link = existing or already or TelegramLink(telegram_user_id=telegram_user_id, user_id=user.id)
            link.telegram_user_id = telegram_user_id
            link.user_id = user.id
            link.telegram_username = username or link.telegram_username
            link.linked_at = utcnow_dt()
            s.add(link)
            s.commit()
            return user

    def find_by_telegram(self, telegram_user_id) -> Optional[User]:
        """Return the Veyra account bound to a Telegram user_id, or None."""
        with self._factory() as s:
            row = (
                s.query(TelegramLink)
                .filter_by(telegram_user_id=telegram_user_id)
                .first()
            )
            if row is None:
                return None
            return s.query(User).filter_by(id=row.user_id).first()

    def grant_by_telegram(self, telegram_user_id, days: Optional[int] = None) -> User:
        """Grant premium to the account linked to a Telegram user_id.

        Used by the merchant loop when a Stars subscription is paid/renewed.
        Raises ``EntitlementError`` if no account is linked yet.
        """
        user = self.find_by_telegram(telegram_user_id)
        if user is None:
            raise EntitlementError(
                "Telegram identity is not linked to a Veyra account; send your account email to the bot first"
            )
        return self.grant_premium(user.email, days=days)

    # ---- checks ------------------------------------------------------------
    def revoke_lapsed(self) -> int:
        """Downgrade every premium account whose subscription has lapsed.

        A Stars subscription that is not renewed simply does not pay again; the
        merchant reconcile pass converts that absence into a ''free'' downgrade.
        Admin accounts are never downgraded (they stay entitled). Returns the
        number of downgrades performed.
        """
        now = utcnow_dt()
        count = 0
        with self._factory() as s:
            rows = s.query(User).filter(User.plan == PLAN_PREMIUM).all()
            for user in rows:
                if user.role == "admin":
                    continue
                exp = user.subscription_expires_at
                if exp is None:
                    continue
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if exp <= now:
                    user.plan = PLAN_FREE
                    user.subscription_expires_at = None
                    count += 1
            s.commit()
        return count

    def count_premium(self) -> int:
        """Number of accounts currently holding an active premium entitlement."""
        with self._factory() as s:
            return (
                s.query(User)
                .filter(User.plan == PLAN_PREMIUM)
                .filter(
                    (User.role == "admin")
                    | (User.subscription_expires_at.is_(None))
                    | (User.subscription_expires_at > utcnow_dt())
                )
                .count()
            )
    @staticmethod
    def _is_admin(user: Optional[User]) -> bool:
        return bool(user and user.role == "admin")

    @classmethod
    def is_active_premium(cls, user: Optional[User]) -> bool:
        """True if an account is entitled to premium features *right now*."""
        if user is None:
            return False
        if cls._is_admin(user):
            return True
        if user.plan != PLAN_PREMIUM:
            return False
        exp = user.subscription_expires_at
        if exp is None:
            return True
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        return exp > utcnow_dt()

    def can_use(self, user_id: int, feature: str) -> bool:
        """Check an entitlement for a (possibly anonymous) account."""
        if feature == "telegram_alerts":
            return self.is_active_premium(self.get(user_id))
        return False

    def status(self, user_id: int) -> dict:
        """Plain, honest entitlement summary for display (never fabricates a plan)."""
        user = self.get(user_id)
        if user is None:
            return {"authenticated": False, "plan": None, "premium": False}
        return {
            "authenticated": True,
            "email": user.email,
            "role": user.role,
            "plan": user.plan,
            "premium": self.is_active_premium(user),
            "expires_at": (
                user.subscription_expires_at.isoformat()
                if user.subscription_expires_at
                else None
            ),
            "telegram_alerts": self.is_active_premium(user),
            "note": "premium is paid via Telegram Stars subscriptions (Phase 10)",
        }