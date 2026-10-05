"""Phase 10 resilience + prod-gate tests.

Uses a temporary on-disk SQLite database via env settings so the backup path
exercises a real file. Prod-secret gating is asserted through Settings built
with env vars forced per-test.
"""

from __future__ import annotations

import pytest

from veyra.config import Settings, redact_secret, validate_production

# --- secret redaction --------------------------------------------------------


def test_redact_secret_masks_and_marks():
    assert redact_secret("") == "<unset>"
    assert redact_secret("password") == "<placeholder>"
    assert redact_secret("supersecretvalue") == "...alue"
    assert redact_secret("ab") == "<redacted>"


# --- prod gate ---------------------------------------------------------------


def _apply_env(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def test_prod_rejects_placeholder_password(monkeypatch):
    _apply_env(monkeypatch,
               VEYRA_ENVIRONMENT="prod", VEYRA_ADMIN_EMAIL="a@b.co",
               VEYRA_ADMIN_PASSWORD="password",
               VEYRA_TELEGRAM_BOT_TOKEN="123:real")
    with pytest.raises(RuntimeError):
        validate_production(Settings())


def test_prod_rejects_missing_bot_token_when_telegram_enabled(monkeypatch):
    _apply_env(monkeypatch,
               VEYRA_ENVIRONMENT="prod", VEYRA_ADMIN_EMAIL="a@b.co",
               VEYRA_ADMIN_PASSWORD="reals3cret",
               VEYRA_POSTBACK_TELEGRAM_ENABLED="true",
               VEYRA_TELEGRAM_BOT_TOKEN="")
    with pytest.raises(RuntimeError):
        validate_production(Settings())


def test_prod_boots_without_telegram_when_sends_off(monkeypatch):
    # Telegram on hold: browser + PWA alerts only, no token required.
    _apply_env(monkeypatch,
               VEYRA_ENVIRONMENT="prod", VEYRA_ADMIN_EMAIL="a@b.co",
               VEYRA_ADMIN_PASSWORD="reals3cret",
               VEYRA_POSTBACK_TELEGRAM_ENABLED="false",
               VEYRA_TELEGRAM_BOT_TOKEN="")
    validate_production(Settings())  # no raise


def test_prod_accepts_real_secrets(monkeypatch):
    _apply_env(monkeypatch,
               VEYRA_ENVIRONMENT="prod", VEYRA_ADMIN_EMAIL="a@b.co",
               VEYRA_ADMIN_PASSWORD="reals3cret",
               VEYRA_TELEGRAM_BOT_TOKEN="123:real-token",
               VEYRA_STARS_PRICE_XTR="750")
    validate_production(Settings())  # no raise


def test_dev_ignores_prod_gate_without_secrets(monkeypatch):
    _apply_env(monkeypatch,
               VEYRA_ENVIRONMENT="dev", VEYRA_ADMIN_PASSWORD="",
               VEYRA_TELEGRAM_BOT_TOKEN="")
    validate_production(Settings())  # no raise in dev


# --- backup (real temp sqlite) ------------------------------------------------


def test_backup_copies_database(settings):
    from veyra.database.backup import backup_database
    from veyra.database.engine import build_engine, init_db, make_session_factory

    engine = build_engine(settings)
    init_db(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        from veyra.database.models import User

        session.add(User(email="x@y.co", password_hash="h" * 20, role="member"))
        session.commit()

    dest = backup_database(settings)
    assert dest.exists()
    assert dest.stat().st_size > 0
    # The backup is a valid, readable SQLite file.
    import sqlite3

    with sqlite3.connect(str(dest)) as con:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
    assert "users" in tables