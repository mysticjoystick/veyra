"""Tests for the API entrypoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from veyra.api.main import app


def test_root_says_online():
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "online"
    assert body["name"] == "veyra"


def test_health_returns_environment():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "online"
    assert body["environment"] in {"dev", "test", "prod"}
    assert "4H" in body["timeframes"]