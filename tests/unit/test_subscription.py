"""Phase 9 subscription entitlement tests (pre-billing).

Confirms premium can be granted/revoked/expired, and that the entitlement gate
honestly decides who may use paid features. No billing is simulated — premium
is provisioned on the account directly.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from veyra.auth import AuthService
from veyra.database.engine import build_engine, init_db, make_session_factory
from veyra.subscribe import EntitlementError, SubscriptionService


@pytest.fixture
def subs(settings):
    engine = build_engine(settings)
    init_db(engine)
    factory = make_session_factory(engine)
    AuthService(factory).create_user("acct@x.co", "password123", role="member")
    return SubscriptionService(factory)


def test_new_account_is_free_and_not_premium(subs):
    user = subs.find_by_email("acct@x.co")
    assert user is not None
    assert subs.status(user.id)["plan"] == "free"
    assert subs.is_active_premium(user) is False


def test_grant_premium_then_is_active(subs):
    subs.grant_premium("acct@x.co")
    user = subs.find_by_email("acct@x.co")
    st = subs.status(user.id)
    assert st["premium"] is True
    assert st["telegram_alerts"] is True
    assert st["plan"] == "premium"


def test_revoke_premium_downgrades(subs):
    subs.grant_premium("acct@x.co")
    subs.revoke_premium("acct@x.co")
    st = subs.status(subs.find_by_email("acct@x.co").id)
    assert st["plan"] == "free"
    assert st["premium"] is False
    assert st["telegram_alerts"] is False


def test_premium_expires_after_days(subs):
    subs.grant_premium("acct@x.co", days=0)
    user = subs.find_by_email("acct@x.co")
    assert subs.is_active_premium(user) is False  # 0 days = lapsed now


def test_premium_with_future_expiry_is_active(subs):
    subs.grant_premium("acct@x.co", days=30)
    user = subs.find_by_email("acct@x.co")
    assert subs.is_active_premium(user) is True
    assert user.subscription_expires_at is not None


def test_unknown_account_raises(subs):
    with pytest.raises(EntitlementError):
        subs.grant_premium("nobody@x.co")
    with pytest.raises(EntitlementError):
        subs.revoke_premium("nobody@x.co")


def test_admin_is_always_premium(subs):
    from veyra.database.models import User

    with subs._factory() as s:
        u = s.query(User).filter_by(email="acct@x.co").first()
        u.role = "admin"
        u.plan = "free"
        u.subscription_expires_at = None
        s.commit()
        u2 = s.query(User).filter_by(email="acct@x.co").first()
        assert subs.is_active_premium(u2) is True


def test_can_use_feature_api(subs):
    subs.grant_premium("acct@x.co")
    uid = subs.find_by_email("acct@x.co").id
    assert subs.can_use(uid, "telegram_alerts") is True
    subs.revoke_premium("acct@x.co")
    assert subs.can_use(uid, "telegram_alerts") is False
    assert subs.can_use(99999, "telegram_alerts") is False  # unknown user


def test_status_shape_is_honest_without_authenticated(subs):
    st = subs.status(99999)
    assert st["authenticated"] is False
    assert st["premium"] is False


# --- Phase 10: Telegram identity link + stars billing hooks -------------


def test_link_telegram_binds_user_and_resolves(subs):
    subs.link_telegram(12345, "acct@x.co")
    assert subs.find_by_telegram(12345).email == "acct@x.co"
    assert subs.find_by_telegram(12345).role == "member"


def test_link_telegram_unknown_email_raises(subs):
    with pytest.raises(EntitlementError):
        subs.link_telegram(12345, "nobody@x.co")


def test_link_telegram_rejects_clashing_email(subs):
    subs.link_telegram(111, "acct@x.co")
    with subs._factory() as s:
        from veyra.database.models import User

        other = User(email="other@x.co", password_hash="x" * 32, role="member")
        s.add(other)
        s.commit()
    with pytest.raises(EntitlementError):
        subs.link_telegram(111, "other@x.co")


def test_grant_by_telegram_provisions_linked_account(subs):
    subs.link_telegram(777, "acct@x.co")
    user = subs.grant_by_telegram(777, days=30)
    assert user.email == "acct@x.co"
    assert user.plan == "premium"
    assert subs.is_active_premium(user) is True


def test_grant_by_telegram_unlinked_raises(subs):
    with pytest.raises(EntitlementError):
        subs.grant_by_telegram(999, days=30)


def test_revoke_lapsed_downgrades_only_expired(subs):
    subs.grant_premium("acct@x.co", days=1)  # active
    with subs._factory() as s:
        from veyra.database.models import User

        u = s.query(User).filter_by(email="acct@x.co").first()
        u.subscription_expires_at = u.subscription_expires_at - timedelta(days=2)
        s.commit()
    assert subs.revoke_lapsed() == 1
    assert subs.count_premium() == 0