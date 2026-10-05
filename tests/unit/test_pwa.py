"""PWA install surface + browser notification hooks (no behavior change to trading)."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from veyra.web.dashboard import create_dashboard
from .test_dashboard import StubAlertService, StubAuthService, StubLiveScan


def _make_client():
    return TestClient(
        create_dashboard(service=StubAlertService(), live_scan=StubLiveScan(), auth_service=StubAuthService())
    )


def test_manifest_is_public_and_valid():
    client = _make_client()
    resp = client.get("/manifest.webmanifest")
    assert resp.status_code == 200
    assert "application/manifest+json" in resp.headers["content-type"]
    data = json.loads(resp.text)
    assert data["short_name"] == "Veyra"
    assert data["start_url"] == "/"
    assert data["display"] == "standalone"
    sizes = {i["sizes"] for i in data["icons"]}
    assert "192x192" in sizes and "512x512" in sizes


def test_service_worker_is_public_with_push_handler():
    client = _make_client()
    resp = client.get("/sw.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
    assert "Service-Worker-Allowed" in resp.headers
    assert "addEventListener" in resp.text
    assert "push" in resp.text
    assert "/api/" in resp.text  # never caches trading data


def test_icons_are_public_png():
    client = _make_client()
    for name in ("icon-192.png", "icon-512.png", "apple-touch-icon.png"):
        resp = client.get(f"/icons/{name}")
        assert resp.status_code == 200, name
        assert "image/png" in resp.headers["content-type"]
        assert resp.content[:8] == b"\x89PNG\r\n\x1a\n", name


def test_unknown_icon_404s():
    client = _make_client()
    assert client.get("/icons/evil.png").status_code == 404


def test_dashboard_links_manifest_and_registers_sw():
    client = _make_client()
    html = client.get("/").text
    assert 'rel="manifest"' in html
    assert "/manifest.webmanifest" in html
    assert "apple-touch-icon" in html
    assert "navigator.serviceWorker.register('/sw.js')" in html
    assert "beforeinstallprompt" in html


def test_dashboard_has_notification_surface():
    client = _make_client()
    html = client.get("/").text
    assert "notifyBell" in html
    assert "VeyraNotify" in html
    assert "setup forms" in html.lower() or "new setup" in html.lower()
    assert "settles" in html.lower()
