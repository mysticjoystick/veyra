"""Phase 10 Telegram Stars billing tests (no network).

Drives the StarsBiller via a fake Bot API (monkeypatched ``_call``) and a fake
subscription service so we never touch Telegram or the DB here.
"""

from __future__ import annotations

from veyra.notify.stars import StarsBiller


class FakeSubs:
    """Mirrors the SubscriptionService surface used by the biller."""

    def __init__(self):
        self.linked = {}
        self.grants = []
        self.link_fail = None
        self.grant_fail = None

    def link_telegram(self, tg_id, email, username=None):
        if self.link_fail:
            raise ValueError(self.link_fail)
        self.linked[tg_id] = email
        return _User(email)

    def grant_by_telegram(self, tg_id, days=None):
        if self.grant_fail:
            raise ValueError(self.grant_fail)
        if tg_id not in self.linked:
            raise ValueError("Telegram identity is not linked to a Veyra account")
        email = self.linked[tg_id]
        self.grants.append((tg_id, days))
        return _User(email)


class _User:
    def __init__(self, email):
        self.email = email
        self.plan = "premium"
        self.role = "member"


class FakeApi:
    def __init__(self):
        self.calls = []

    def respond(self, method=None, result=None, raise_exc=None):
        def inner(self_, params):
            self.calls.append((method or self_.last, params))
            if raise_exc:
                raise raise_exc
            return result
        return inner


def make_biller(fake_subs=None):
    return StarsBiller(
        "123:ABCtoken",
        (lambda: (fake_subs or FakeSubs())),
        price_xtr=500,
        period_seconds=2592000,
        payload_prefix="veyra:premium:",
    )


# --- createMonthlyLink ----------------------------------------------------


def test_create_monthly_link_sets_stars_params(monkeypatch):
    biller = make_biller()
    calls = []

    def fake_call(method, params):
        calls.append((method, params))
        return "https://t.me/$invoice/link"

    monkeypatch.setattr(biller, "_call", fake_call)
    assert biller.create_monthly_link() == "https://t.me/$invoice/link"
    method, params = calls[0]
    assert method == "createInvoiceLink"
    assert params["currency"] == "XTR"
    assert params["provider_token"] == ""
    assert params["subscription_period"] == 2592000
    assert params["prices"][0]["amount"] == 500
    assert params["payload"].startswith("veyra:premium:")


# --- update handling: message with email -----------------------------------


def test_message_email_links_account(monkeypatch):
    fake = FakeSubs()
    biller = make_biller(fake)
    msg = {
        "text": "  acct@x.co  ",
        "from": {"id": 777, "username": "acctuser"},
        "chat": {"id": 999},
    }
    monkeypatch.setattr(biller, "_call", lambda m, p: None)
    res = biller._handle_update({"message": msg})
    assert res["kind"] == "linked"
    assert fake.linked[777] == "acct@x.co"
    assert biller._say  # replied


# --- pre_checkout_query ----------------------------------------------------


def test_pre_checkout_approves_own_payload(monkeypatch):
    biller = make_biller()
    seen = {}

    def fake_call(method, params):
        seen[method] = params
        return {}

    monkeypatch.setattr(biller, "_call", fake_call)
    res = biller._handle_update({
        "pre_checkout_query": {"id": "q1", "invoice_payload": "veyra:premium:monthly"}
    })
    assert res["approved"] is True
    assert seen["answerPreCheckoutQuery"]["ok"] is True


def test_pre_checkout_rejects_foreign_payload(monkeypatch):
    biller = make_biller()
    seen = {}

    def fake_call(method, params):
        seen[method] = params
        return {}

    monkeypatch.setattr(biller, "_call", fake_call)
    res = biller._handle_update({
        "pre_checkout_query": {"id": "q1", "invoice_payload": "some:other:thing"}
    })
    assert res["approved"] is False
    assert seen["answerPreCheckoutQuery"]["ok"] is False


# --- successful_payment -----------------------------------------------------


def test_successful_payment_provisions_linked_account(monkeypatch):
    fake = FakeSubs()
    biller = make_biller(fake)
    biller._handle_update({"message": {"text": "acct@x.co", "from": {"id": 777}, "chat": {"id": 999}}})
    sp = {
        "invoice_payload": "veyra:premium:monthly",
        "telegram_payment_charge_id": "chg_1",
        "from": {"id": 777},
        "is_first_recurring": True,
    }
    res = biller._handle_update({"message": {"successful_payment": sp}})
    assert res["kind"] == "provisioned"
    assert res["email"] == "acct@x.co"
    assert res["charge_id"] == "chg_1"
    assert res["is_first"] is True
    assert fake.grants and fake.grants[0][0] == 777


def test_successful_payment_ignores_foreign_payload(monkeypatch):
    fake = FakeSubs()
    biller = make_biller(fake)
    sp = {"invoice_payload": "other", "from": {"id": 1}}
    res = biller._handle_update({"message": {"successful_payment": sp}})
    assert res["kind"] == "ignored_payment"
    assert fake.grants == []


def test_successful_payment_unlinked_reports_unprovisioned(monkeypatch):
    fake = FakeSubs()
    biller = make_biller(fake)
    sp = {"invoice_payload": "veyra:premium:monthly", "from": {"id": 404}}
    res = biller._handle_update({"message": {"successful_payment": sp}})
    assert res["kind"] == "unprovisioned"


# --- poll_once cursor --------------------------------------------------------


def test_poll_once_advances_offset(monkeypatch):
    biller = make_biller()
    updates = [{"update_id": 5, "message": {"text": "hi"}}]

    def fake_call(method, params):
        assert params["offset"] == 1  # first poll starts offset+1
        return updates

    monkeypatch.setattr(biller, "_call", lambda m, p: fake_call(m, p) if m == "getUpdates" else None)
    summary = biller.poll_once()
    assert summary["updates"] == 1
    # Next poll starts from last offset+1 (6).
    captured = {}

    def fake_call2(method, params):
        captured["offset"] = params["offset"]
        return []

    monkeypatch.setattr(biller, "_call", fake_call2)
    biller.poll_once()
    assert captured["offset"] == 6