"""Setup lifecycle state machine.

Defines the only legal transitions between SetupState values. All setup
lifecycle progression must pass through this module so that arbitrary state
moves (e.g. DETECTED -> COMPLETED) are rejected by construction.

Terminal states (INVALIDATED / EXPIRED / COMPLETED) have no outgoing edges.
"""

from __future__ import annotations

from ..domain import SetupState


# Legal transitions: from-state -> set(legal to-states).
_PATHS: dict[SetupState, frozenset[SetupState]] = {
    SetupState.DETECTED: frozenset(
        {SetupState.DEVELOPING, SetupState.INVALIDATED, SetupState.EXPIRED}
    ),
    SetupState.DEVELOPING: frozenset(
        {SetupState.QUALIFIED, SetupState.INVALIDATED, SetupState.EXPIRED}
    ),
    SetupState.QUALIFIED: frozenset(
        {SetupState.TRIGGERED, SetupState.INVALIDATED, SetupState.EXPIRED}
    ),
    SetupState.TRIGGERED: frozenset(
        {SetupState.COMPLETED, SetupState.INVALIDATED, SetupState.EXPIRED}
    ),
    # Terminal states.
    SetupState.INVALIDATED: frozenset(),
    SetupState.EXPIRED: frozenset(),
    SetupState.COMPLETED: frozenset(),
}

_TERMINAL = frozenset(
    {SetupState.INVALIDATED, SetupState.EXPIRED, SetupState.COMPLETED}
)


def can_transition(current: SetupState, new: SetupState) -> bool:
    """Return True if `new` is a legal transition from `current`."""
    return new in _PATHS.get(current, frozenset())


def legal_transitions(current: SetupState) -> frozenset[SetupState]:
    return _PATHS.get(current, frozenset())


def is_terminal(state: SetupState) -> bool:
    return state in _TERMINAL


def transition(current: SetupState, new: SetupState) -> SetupState:
    """Transition `current` -> `new`, raising on an illegal move.

    Returns `new` for convenience.
    """
    if current == new:
        return current
    if not can_transition(current, new):
        raise ValueError(
            f"Illegal setup state transition: {current.value} -> {new.value}"
        )
    return new