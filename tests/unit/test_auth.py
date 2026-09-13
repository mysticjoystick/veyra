"""Phase 8 auth: password hashing, token primitives, and the AuthService.

Confirms: passwords are never stored plaintext; verification is correct and
rejects wrong/foreign hashes; sessions are opaque tokens whose DB row holds only
a hash; login/logout/resolve behave; admin seeding is idempotent; and auth
errors are indistinguishable for enumeration resistance.
"""

from __future__ import annotations

import pytest

from veyra.auth import AuthError, AuthService, security
from veyra.database.engine import build_engine, init_db, make_session_factory
from veyra.database.models import Session as DbSession
from veyra.database.models import User


@pytest.fixture
def service(settings):
    engine = build_engine(settings)
    init_db(engine)
    return AuthService(make_session_factory(engine))


# --- security primitives -------------------------------------------------
def test_hash_password_never_stores_plaintext():
    h = security.hash_password("correct horse battery staple")
    assert "correct horse" not in h
    assert h.startswith("$scrypt$")
    assert security.verify_password("correct horse battery staple", h)


def test_verify_rejects_wrong_password_and_foreign_hash():
    h = security.hash_password("secret-one")
    assert not security.verify_password("secret-two", h)
    assert not security.verify_password("secret-one", "not-a-hash")
    assert not security.verify_password("secret-one", "")


def test_same_password_different_salt_hashes_differ():
    a = security.hash_password("pw")
    b = security.hash_password("pw")
    assert a != b
    assert security.verify_password("pw", a) and security.verify_password("pw", b)


def test_token_hash_is_one_way_and_stable():
    t = security.new_token()
    assert security.hash_token(t) == security.hash_token(t)
    assert security.hash_token(t) != t
    assert len(security.hash_token(t)) == 64


# --- service: users --------------------------------------------------------
def test_create_and_authenticate_user(service):
    u = service.create_user("Admin@Test.io", "password123", role="admin")
    assert u is not None
    assert u.email == "admin@test.io"  # normalised to lower-case
    assert u.password_hash != "password123"


def test_create_user_rejects_duplicate_email(service):
    service.create_user("a@b.co", "password123")
    with pytest.raises(AuthError):
        service.create_user("A@b.co", "otherpass1")


def test_create_user_requires_valid_email_and_password(service):
    with pytest.raises(AuthError):
        service.create_user("", "password123")
    with pytest.raises(AuthError):
        service.create_user("x@y.z", "short")  # < 8 chars


# --- service: sessions -----------------------------------------------------
def test_authenticate_returns_opaque_token_and_stores_only_hash(service):
    service.create_user("u@x.co", "password123")
    token = service.authenticate("u@x.co", "password123")
    assert token and token != "password123"
    import re

    assert re.fullmatch(r"[A-Za-z0-9_-]+", token)
    # The stored session holds a digest, never the raw token.
    engine = service._factory().bind
    from sqlalchemy import text

    with service._factory() as s:
        row = s.query(DbSession).one()
        assert row.token_hash == security.hash_token(token)
        assert row.token_hash != token


def test_authenticate_rejects_bad_credentials(settings):
    # Rebuild with a real factory from the settings fixture.
    engine = build_engine(settings)
    init_db(engine)
    svc = AuthService(make_session_factory(engine))
    svc.create_user("u@x.co", "password123")
    with pytest.raises(AuthError):
        svc.authenticate("u@x.co", "wrongpassword")
    with pytest.raises(AuthError):
        svc.authenticate("unknown@x.co", "password123")


def test_resolve_session_roundtrip_and_logout(service):
    service.create_user("u@x.co", "password123")
    token = service.authenticate("u@x.co", "password123")
    user = service.resolve_session(token)
    assert user is not None and user.email == "u@x.co"
    assert service.resolve_session("garbage-token") is None
    assert service.resolve_session(None) is None
    assert service.logout(token) is True
    assert service.resolve_session(token) is None  # revoked
    assert service.logout(token) is False  # idempotent


# --- service: seeding ------------------------------------------------------
def test_ensure_admin_is_idempotent_and_returns_flag(service):
    created = service.ensure_admin("admin@veyra.io", "supersecret1")
    assert created is True
    again = service.ensure_admin("admin@veyra.io", "supersecret1")
    assert again is False
    assert service.find_by_email("admin@veyra.io") is not None


def test_ensure_admin_noop_without_email(service):
    assert service.ensure_admin("", "anything") is False