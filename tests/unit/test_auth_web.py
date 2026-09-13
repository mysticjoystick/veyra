"""Phase 8 web auth: the command-center is gated behind a session.

Uses the real AuthService + a real (tmp) DB so we exercise the actual
cookie/session flow: unauthenticated requests are rejected, login issues a
session cookie, and that cookie opens the gated endpoints. Dashboard services
are stubbed to keep the test fast and avoid heavy compute.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from veyra.auth import AuthService
from veyra.database.engine import build_engine, init_db, make_session_factory
from veyra.subscribe import SubscriptionService
from veyra.web.dashboard import create_dashboard

from tests.unit.test_dashboard import StubAlertService, StubLiveScan


@pytest.fixture
def client(settings):
    engine = build_engine(settings)
    init_db(engine)
    factory = make_session_factory(engine)
    auth = AuthService(factory, session_ttl_seconds=300)
    auth.create_user("admin@test.io", "password123", role="admin")
    app = create_dashboard(
        service=StubAlertService(),
        live_scan=StubLiveScan(),
        auth_service=auth,
        subscription_service=SubscriptionService(factory),
    )
    return TestClient(app)


def _login(client) -> None:
    r = client.post(
        "/login",
        data={"email": "admin@test.io", "password": "password123", "next": "/"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "veyra_session" in client.cookies


# --- gating ----------------------------------------------------------------
def test_unauthenticated_requests_are_rejected(client):
    # Browser paths redirect to /login (303), API paths return 401.
    for path in ["/"]:
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code == 303, path
    for path in ["/api/alerts", "/api/live", "/api/status", "/api/latest", "/api/portfolio"]:
        resp = client.get(path)
        assert resp.status_code == 401, path


def test_login_page_renders(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "Sign in" in resp.text


def test_bad_credentials_are_rejected(client):
    resp = client.post("/login", data={"email": "admin@test.io", "password": "wrong", "next": "/"})
    assert resp.status_code == 401


# --- happy-path ------------------------------------------------------------
def test_login_then_gated_routes_open(client):
    _login(client)
    assert client.get("/").status_code == 200
    assert client.get("/api/alerts").status_code == 200
    assert client.get("/api/status").status_code == 200


def test_logout_revokes_session(client):
    _login(client)
    assert client.get("/api/status").status_code == 200
    client.get("/logout")
    assert client.get("/api/status").status_code == 401


def test_session_endpoint_reports_auth_state(client):
    assert client.get("/api/session").json()["authenticated"] is False
    _login(client)
    assert client.get("/api/session").json()["authenticated"] is True


def test_session_endpoint_reports_entitlement_for_admin(client):
    _login(client)
    j = client.get("/api/session").json()
    assert j["authenticated"] is True
    ent = j.get("entitlements")
    assert ent is not None
    # Admin accounts are always entitled to paid features (Telegram alerts).
    assert ent["premium"] is True
    assert ent["telegram_alerts"] is True


# --- Phase 10: brute-force protection + secure cookies --------------------


def test_session_cookie_is_httponly_samesite_lax_not_secure_in_test(client):
    """Test env may run over plain http://127.0.0.1, so Secure must be off."""
    _login(client)
    assert client.cookies.get("veyra_session")  # cookie present
    # Pull the actual Set-Cookie header from a fresh login and check the flags.
    r = client.post(
        "/login",
        data={"email": "admin@test.io", "password": "password123", "next": "/"},
        follow_redirects=False,
    )
    set_cookie = r.headers.get("set-cookie", "")
    assert "httponly" in set_cookie.lower()
    assert "samesite" in set_cookie.lower()
    assert "secure" not in set_cookie.lower()  # off in test/dev


def test_login_rate_limited_after_repeated_failures(client):
    for _ in range(10):
        client.post("/login", data={"email": "admin@test.io", "password": "nope", "next": "/"})
    resp = client.post("/login", data={"email": "admin@test.io", "password": "nope", "next": "/"})
    assert resp.status_code == 429