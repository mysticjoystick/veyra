"""Veyra authentication package (Phase 8).

Provides local email+password authentication with hashed credentials and
opaque bearer sessions. This phase seeds a single admin account; general
signup stays closed until launch. Phase 9 layers subscription entitlements
on top of these accounts.
"""

from __future__ import annotations

from . import security
from .router import (
    SESSION_COOKIE,
    build_auth_router,
    current_user,
    get_auth_service,
    require_user,
)
from .service import AuthError, AuthService, UserNotFound, SESSION_TTL_SECONDS

__all__ = [
    "security",
    "AuthService",
    "AuthError",
    "UserNotFound",
    "SESSION_TTL_SECONDS",
    "SESSION_COOKIE",
    "build_auth_router",
    "current_user",
    "get_auth_service",
    "require_user",
]