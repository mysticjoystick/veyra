"""Phase 10 auth brute-force protection tests."""

from __future__ import annotations

from veyra.auth.ratelimit import LoginLimiter, SlidingWindowLimiter


def test_limiter_allows_up_to_max_events():
    lim = SlidingWindowLimiter(max_events=3, window_seconds=60)
    assert lim.allow("k") is True
    assert lim.allow("k") is True
    assert lim.allow("k") is True
    assert lim.allow("k") is False


def test_limiter_keys_are_independent():
    lim = SlidingWindowLimiter(max_events=1, window_seconds=60)
    assert lim.allow("a") is True
    assert lim.allow("a") is False
    # A different key has its own budget.
    assert lim.allow("b") is True


def test_limiter_reset_clears_budget():
    lim = SlidingWindowLimiter(max_events=1, window_seconds=60)
    assert lim.allow("k") is True
    assert lim.allow("k") is False
    lim.reset()
    assert lim.allow("k") is True


def test_login_limiter_default_rejects_after_10():
    lim = LoginLimiter()
    assert all(lim.allow("k") for _ in range(10))
    assert lim.allow("k") is False


def test_limiter_rejects_non_positive_max():
    import pytest

    with pytest.raises(ValueError):
        SlidingWindowLimiter(max_events=0)