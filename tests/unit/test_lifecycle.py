"""Tests for the setup lifecycle state machine."""

from __future__ import annotations

import pytest

from veyra.domain import SetupState
from veyra.strategy.lifecycle import (
    can_transition,
    is_terminal,
    legal_transitions,
    transition,
)


def test_detected_can_develop_or_invalidate_or_expire():
    paths = legal_transitions(SetupState.DETECTED)
    assert SetupState.DEVELOPING in paths
    assert SetupState.INVALIDATED in paths
    assert SetupState.EXPIRED in paths
    assert SetupState.COMPLETED not in paths
    assert SetupState.TRIGGERED not in paths


def test_developing_to_qualified():
    assert can_transition(SetupState.DEVELOPING, SetupState.QUALIFIED)
    assert transition(SetupState.DEVELOPING, SetupState.QUALIFIED) == SetupState.QUALIFIED


def test_qualified_to_triggered_then_completed():
    assert can_transition(SetupState.QUALIFIED, SetupState.TRIGGERED)
    assert transition(SetupState.QUALIFIED, SetupState.TRIGGERED) == SetupState.TRIGGERED
    assert transition(SetupState.TRIGGERED, SetupState.COMPLETED) == SetupState.COMPLETED


def test_terminal_states_have_no_outgoing_edges():
    for terminal in (SetupState.INVALIDATED, SetupState.EXPIRED, SetupState.COMPLETED):
        assert is_terminal(terminal)
        assert legal_transitions(terminal) == frozenset()


def test_illegal_transition_raises():
    with pytest.raises(ValueError):
        transition(SetupState.DETECTED, SetupState.COMPLETED)
    with pytest.raises(ValueError):
        transition(SetupState.INVALIDATED, SetupState.DETECTED)


def test_identity_transition_is_noop():
    assert transition(SetupState.DETECTED, SetupState.DETECTED) == SetupState.DETECTED


def test_allowed_full_path():
    s = SetupState.DETECTED
    for nxt in (
        SetupState.DEVELOPING,
        SetupState.QUALIFIED,
        SetupState.TRIGGERED,
        SetupState.INVALIDATED,
    ):
        s = transition(s, nxt)
    assert is_terminal(s)