"""Account + session auth service (Phase 8).

Thin service over the ``User``/``Session`` ORM entities. Responsibilities:

* create / verify credentials (password hashing via ``veyra.auth.security``)
* issue and validate opaque bearer sessions (only the token hash is stored)
* seed the initial admin account (idempotent) so the operator can log in

Auth is kept local and self-contained for this phase. Phase 9 attaches
subscription entitlements; Phase 10 moves secrets/token transport to
production hardening.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session as OrmSession

from . import security
from ..database.models import Session as DbSession
from ..database.models import User

SESSION_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days


class AuthError(Exception):
    """Base auth error (e.g. bad email/password, disabled account)."""


class UserNotFound(AuthError):
    pass


def utcnow_dt() -> datetime:
    return datetime.now(timezone.utc)


class AuthService:
    def __init__(self, session_factory, session_ttl_seconds: Optional[int] = None) -> None:
        self._factory = session_factory
        self.session_ttl_seconds = session_ttl_seconds or SESSION_TTL_SECONDS

    def _session(self) -> OrmSession:
        return self._factory()

    # ---- users --------------------------------------------------------
    def create_user(self, email: str, password: str, role: str = "member") -> User:
        """Create a user, returning it. Raises ``AuthError`` if the email is taken."""
        email = (email or "").strip().lower()
        if not email or "@" not in email:
            raise AuthError("a valid email is required")
        if not password or len(password) < 8:
            raise AuthError("password must be at least 8 characters")
        with self._session() as s:
            exists = s.query(User).filter_by(email=email).first()
            if exists:
                raise AuthError("email already registered")
            user = User(
                email=email,
                password_hash=security.hash_password(password),
                role=role,
            )
            s.add(user)
            s.commit()
            s.refresh(user)
            return user

    def get_user(self, user_id: int) -> Optional[User]:
        with self._session() as s:
            return s.query(User).filter_by(id=user_id).first()

    def find_by_email(self, email: str) -> Optional[User]:
        with self._session() as s:
            return s.query(User).filter_by(email=(email or "").strip().lower()).first()

    # ---- login / logout -------------------------------------------------
    def authenticate(self, email: str, password: str) -> str:
        """Verify credentials and return a fresh bearer token.

        Raises ``AuthError`` on unknown email or bad password (indistinguishable
        on purpose, so enumeration is not rewarding).
        """
        user = self.find_by_email(email)
        if user is None or not security.verify_password(password, user.password_hash):
            raise AuthError("invalid email or password")
        token = security.new_token()
        token_hash = security.hash_token(token)
        expires = utcnow_dt() + timedelta(seconds=self.session_ttl_seconds)
        with self._session() as s:
            # Re-fetch in this session context to attach reliably.
            row = s.query(User).filter_by(id=user.id).first()
            db = DbSession(user_id=row.id, token_hash=token_hash, expires_at=expires)
            s.add(db)
            s.commit()
        return token

    def logout(self, token: str) -> bool:
        """Revoke a session by raw bearer token. Idempotent.

        Returns True only when a session was actually revoked (i.e. not already
        revoked/absent). Repeated logout of the same token returns False.
        """
        token_hash = security.hash_token(token)
        with self._session() as s:
            row = s.query(DbSession).filter_by(token_hash=token_hash).first()
            if row is None or row.revoked:
                return False
            row.revoked = 1
            s.commit()
            return True

    def resolve_session(self, token: Optional[str]) -> Optional[User]:
        """Return the authenticated user for a bearer token, or None.

        Never raises for invalid/expired tokens — absence is a normal outcome.
        """
        if not token:
            return None
        token_hash = security.hash_token(token)
        now = utcnow_dt()
        with self._session() as s:
            row = (
                s.query(DbSession)
                .filter_by(token_hash=token_hash, revoked=0)
                .filter(DbSession.expires_at > now)
                .first()
            )
            if row is None:
                return None
            user = s.query(User).filter_by(id=row.user_id).first()
            return user

    # ---- seeding ----------------------------------------------------------
    def ensure_admin(self, email: str, password: str) -> bool:
        """Idempotently create the operator admin account if none exists.

        Returns True if a new admin was created, False if one already existed.
        No-op (False) when the config does not request seeding (email empty).
        """
        email = (email or "").strip().lower()
        if not email:
            return False
        existing = self.find_by_email(email)
        if existing is not None:
            return False
        self.create_user(email=email, password=password, role="admin")
        return True