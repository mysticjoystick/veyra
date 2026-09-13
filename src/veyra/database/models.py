"""Persistence entities for Veyra.

These SQLAlchemy models store the auditable history of Veyra's decisions,
including failures and invalidations. They mirror the clean domain models
in veyra.domain.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Integer,
    Float,
    String,
    Text,
    BigInteger,
    DateTime,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Candle(Base):
    __tablename__ = "candles"
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "open_time", name="uq_candle_key"),
    )

    id = Column(Integer, primary_key=True)
    symbol = Column(String(32), nullable=False, index=True)
    timeframe = Column(String(8), nullable=False, index=True)
    open_time = Column(BigInteger, nullable=False, index=True)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, nullable=False)


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id = Column(Integer, primary_key=True)
    symbol = Column(String(32), nullable=False, index=True)
    timeframe = Column(String(8), nullable=False, index=True)
    timestamp = Column(BigInteger, nullable=False, index=True)
    regime = Column(String(16), nullable=False, default="UNKNOWN")
    overall_score = Column(Integer, nullable=False, default=0)
    system_state = Column(String(16), nullable=False, default="WAIT")
    scores_json = Column(Text, nullable=False, default="{}")


class Setup(Base):
    __tablename__ = "setups"

    id = Column(Integer, primary_key=True)
    symbol = Column(String(32), nullable=False, index=True)
    timeframe = Column(String(8), nullable=False, index=True)
    timestamp = Column(BigInteger, nullable=False, index=True)
    setup_type = Column(String(32), nullable=False)
    side = Column(String(8), nullable=False)
    regime = Column(String(16), nullable=False, default="UNKNOWN")
    overall_score = Column(Integer, nullable=False, default=0)
    scores_json = Column(Text, nullable=False, default="{}")
    interest_area_low = Column(Float, nullable=True)
    interest_area_high = Column(Float, nullable=True)
    invalidation = Column(Text, nullable=True)
    targets_json = Column(Text, nullable=False, default="[]")
    state = Column(String(16), nullable=False, default="DETECTED")
    reasoning = Column(Text, nullable=False, default="")
    expiry_condition = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class SetupEvent(Base):
    __tablename__ = "setup_events"

    id = Column(Integer, primary_key=True)
    setup_id = Column(Integer, ForeignKey("setups.id"), nullable=False, index=True)
    event_type = Column(String(32), nullable=False)
    timestamp = Column(BigInteger, nullable=False, index=True)
    detail = Column(Text, nullable=True)

    setup = relationship("Setup", backref="events")


class DatasetRecord(Base):
    """Provenance metadata for a market dataset.

    Identifies a dataset via (market, timeframe) and records how it was
    retrieved so future backtests are reproducible and not coupled to a
    live provider's current output.
    """

    __tablename__ = "datasets"
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", name="uq_dataset_key"),
    )

    id = Column(Integer, primary_key=True)
    symbol = Column(String(32), nullable=False, index=True)
    timeframe = Column(String(8), nullable=False, index=True)
    provider = Column(String(64), nullable=False)
    start_time = Column(BigInteger, nullable=True)   # inclusive, epoch UTC s
    end_time = Column(BigInteger, nullable=True)     # inclusive, epoch UTC s
    candle_count = Column(Integer, nullable=False, default=0)
    normalization_version = Column(String(32), nullable=False, default="1")
    provider_version = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)


class User(Base):
    """An account that may view the command-center (Phase 8).

    Passwords are never stored in plaintext; ``password_hash`` holds the
    self-describing scrypt digest from ``veyra.auth.security``.
    ``role`` is a coarse-grain gate ("admin" for now); subscription status is
    a separate Phase 9 concern and lives on the user rather than hard-wired
    here so entitlements can evolve independently. A user owns many sessions.
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_user_email"),
    )

    id = Column(Integer, primary_key=True)
    email = Column(String(255), nullable=False, index=True)
    password_hash = Column(Text, nullable=False)
    role = Column(String(32), nullable=False, default="member")
    # --- Phase 9 entitlements -------------------------------------------
    # plan is the coarse subscription tier ("free" default, "premium" = paid).
    # subscription_expires_at is when a paid plan lapses (None = perpetual).
    plan = Column(String(32), nullable=False, default="free")
    subscription_expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    sessions = relationship("Session", back_populates="user", cascade="all, delete-orphan")


class Session(Base):
    """An authenticated session (Phase 8).

    ``token_hash`` stores the SHA-256 digest of the bearer token; the raw token
    is only ever given to the client once, at login. Expired or invalidated
    sessions are ignored by the auth service.
    """

    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_session_token"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    expires_at = Column(DateTime, nullable=False)
    revoked = Column(Integer, nullable=False, default=0)

    user = relationship("User", back_populates="sessions")


class TelegramLink(Base):
    """Binds a Telegram user_id to a Veyra account (Phase 10 Stars billing).

    A subscriber proves ownership of their Veyra account by sending their
    account email to the merchant bot; from then on `telegram_user_id` is the
    identity used to attribute Stars subscription payments, renewals and
    cancellations. One Telegram identity maps to at most one Veyra account.
    """

    __tablename__ = "telegram_links"
    __table_args__ = (
        UniqueConstraint("telegram_user_id", name="uq_telegram_user"),
        UniqueConstraint("user_id", name="uq_telegram_link_user"),
    )

    id = Column(Integer, primary_key=True)
    telegram_user_id = Column(BigInteger, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    telegram_username = Column(String(255), nullable=True)
    linked_at = Column(DateTime, nullable=False, default=utcnow)

    user = relationship("User", backref="telegram_link")


def __str__(self) -> str:  # pragma: no cover
    return f"<Setup {self.symbol} {self.timeframe} {self.id}>"
