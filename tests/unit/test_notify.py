"""Tests for Phase 7 notifications (send-safe by default)."""

from __future__ import annotations

import logging

import pytest

from veyra.notify import NotifyClerk, TelegramNotifier, render_message, setup_state_for


def test_notifier_default_is_dry_run_no_network():
    """With nothing configured, send() logs and never touches the network."""
    notifier = TelegramNotifier()
    assert notifier.configured is False
    # Capture the dry-log so we can assert the message was only journaled.
    captured = {}

    class _Sniff(logging.Handler):
        def emit(self, record):
            captured["msg"] = record.getMessage()

    handler = _Sniff()
    logger = logging.getLogger("veyra.notify")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        status = notifier.send("Hello world")
    finally:
        logger.removeHandler(handler)
    assert status["sent"] is False
    assert status["dry_run"] is True
    assert captured.get("msg", "")


def test_notifier_configured_requires_all_three():
    no_chat = TelegramNotifier(bot_token="tok", dry_run=False)
    assert no_chat.configured is False
    no_token = TelegramNotifier(chat_id="123", dry_run=False)
    assert no_token.configured is False
    ok = TelegramNotifier(bot_token="tok", chat_id="123", dry_run=False)
    assert ok.configured is True


def test_notifier_empty_message_is_rejected():
    notifier = TelegramNotifier()
    status = notifier.send("   ")
    assert status["sent"] is False
    assert status["error"] == "empty message"


def test_setup_state_for_maps_outcome():
    assert setup_state_for("COMPLETED", None, None) == "COMPLETED"
    assert setup_state_for(None, "CONVERGENT", 90) == "QUALIFIED"
    assert setup_state_for(None, "EMERGENT", 45) == "DEVELOPING"
    assert setup_state_for(None, "SCANNED", 20) == "DETECTED"
    # Unrecognised stage passes through untouched (never hidden).
    assert setup_state_for("FOO", None, None) == "FOO"


def test_clerk_emits_only_state_changes(tmp_path):
    clerk = NotifyClerk(state_path=tmp_path / "state.json")
    datasets = [
        {
            "symbol": "BTC/USDT",
            "timeframe": "4H",
            "alerts": [
                {
                    "setup_key": "a",
                    "setup_type": "TREND_CONTINUATION",
                    "side": "LONG",
                    "level": "CONVERGENT",
                    "score_normalized": 90,
                    "outcome": "QUALIFIED_NO_TRADE",
                }
            ],
        }
    ]
    first = clerk.scan(datasets)
    assert len(first) == 1
    assert first[0]["state"] == "QUALIFIED_NO_TRADE"

    # A second run with no changes must emit nothing (idempotent).
    second = clerk.scan(datasets)
    assert second == []


def test_clerk_reports_transition(tmp_path):
    clerk = NotifyClerk(state_path=tmp_path / "state.json")
    row = {"symbol": "ETH/USDT", "timeframe": "15m", "alerts": [
        {"setup_key": "x", "level": "EMERGENT", "score_normalized": 44}
    ]}
    clerk.scan([row])
    row["alerts"][0]["score_normalized"] = 90
    row["alerts"][0]["level"] = "CONVERGENT"
    changes = clerk.scan([row])
    assert len(changes) == 1
    assert changes[0]["state"] == "QUALIFIED"


def test_clerk_min_change_filters(tmp_path):
    clerk = NotifyClerk(state_path=tmp_path / "state.json", min_change="QUALIFIED")
    datasets = [{"symbol": "S", "timeframe": "1D", "alerts": [
        {"setup_key": "k", "level": "EMERGENT", "score_normalized": 44}
    ]}]
    assert clerk.scan(datasets) == []


def test_render_message_is_plain_and_sim_tagged():
    row = {
        "symbol": "BTC/USDT",
        "timeframe": "4H",
        "setup_type": "TREND_CONTINUATION",
        "side": "LONG",
        "level": "CONVERGENT",
        "score": 90,
        "state": "QUALIFIED",
        "interest_area_low": 50000.0,
        "interest_area_high": 51000.0,
        "invalidation": "close below 49k",
        "reasoning": "trend continuation in bull",
    }
    text = render_message(row)
    assert "not an order" in text.lower()
    assert "BTC/USDT" in text
    assert "live" in text.lower()


def test_clerk_scan_live_emits_new_setup_then_silent(tmp_path):
    clerk = NotifyClerk(state_path=tmp_path / "state.json")
    markets = [
        {
            "symbol": "BTC/USDT",
            "timeframe": "15m",
            "setups": [
                {
                    "setup_type": "PULLBACK",
                    "side": "LONG",
                    "timestamp": 1788000000,
                    "overall_score": 52,
                    "state": "DEVELOPING",
                    "interest_area": {"low": 78920.0, "high": 80408.0},
                    "invalidation": "Close below 78920",
                    "reasoning": "PULLBACK LONG BULL",
                }
            ],
        }
    ]
    first = clerk.scan_live(markets)
    assert len(first) == 1
    assert first[0]["state"] == "DEVELOPING"
    assert first[0]["score"] == 52

    # Identical live scan -> steady state, nothing new to send.
    assert clerk.scan_live(markets) == []

    # State progresses -> a change is emitted.
    markets[0]["setups"][0]["overall_score"] = 90
    markets[0]["setups"][0]["state"] = "QUALIFIED"
    progressed = clerk.scan_live(markets)
    assert len(progressed) == 1
    assert progressed[0]["state"] == "QUALIFIED"


def test_clerk_scan_live_keys_by_setup_type_side_not_timestamp(tmp_path):
    clerk = NotifyClerk(state_path=tmp_path / "state.json")
    m1 = [{"symbol": "S", "timeframe": "1D", "setups": [
        {"setup_type": "BREAKOUT", "side": "SHORT", "timestamp": 10, "overall_score": 75, "state": "DETECTED"}
    ]}]
    assert len(clerk.scan_live(m1)) == 1
    # Same setup type/side on a new bar (new timestamp) is the SAME alert, so it
    # must stay silent - no spam every new bar.
    m2 = [{"symbol": "S", "timeframe": "1D", "setups": [
        {"setup_type": "BREAKOUT", "side": "SHORT", "timestamp": 11, "overall_score": 76, "state": "DETECTED"}
    ]}]
    assert clerk.scan_live(m2) == []


def test_clerk_scan_live_passes_min_change_filter(tmp_path):
    clerk = NotifyClerk(state_path=tmp_path / "state.json", min_change="QUALIFIED")
    markets = [{"symbol": "S", "timeframe": "1D", "setups": [
        {"setup_type": "BREAKOUT", "side": "LONG", "timestamp": 10, "overall_score": 44, "state": "DEVELOPING"}
    ]}]
    assert clerk.scan_live(markets) == []


# --- Phase 9: entitlement gating -----------------------------------------


def test_notifier_gates_send_for_non_paying():
    n = TelegramNotifier(bot_token="tok", chat_id="123", dry_run=False,
                         require_entitlement=True, entitled=False)
    st = n.send("secret setup news")
    assert st["sent"] is False
    assert st["gated"] is True
    assert "subscription" in st["error"]
    assert n.configured is False


def test_notifier_entitled_but_dry_run_is_never_configured():
    n = TelegramNotifier(bot_token="tok", chat_id="123", dry_run=True,
                         require_entitlement=True, entitled=True)
    # Dry-run is a hard no-send: not configured, not gated.
    assert n.configured is False
    assert n.gated is False


def test_notifier_non_gated_preserves_prior_behaviour():
    n = TelegramNotifier(bot_token="tok", chat_id="123", dry_run=False,
                         require_entitlement=True, entitled=True)
    assert n.configured is True  # entitled + configured = real send possible
    assert n.gated is False