"""Alert selectivity policy.

These rules decide *which* qualified setups deserve an alert. Selectivity is the
point of Veyra alerts: surface the strongest, most defensible opportunities and
silence the rest. The policy is intentionally simple and documented:

  * min_score            : only alert setups at or above this normalised score
  * max_per_window       : cap the number of alerts per rolling time window so a
                           burst of weak setups cannot drown out the list
  * qualifying_states    : a setup must have reached one of these lifecycle
                           states to be alertable

This policy is a reporting aid for humans. It is NOT an execution rule, is not
part of the frozen baseline, and changing it changes no trade.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class AlertPolicy:
    # Selectivity knobs. These are reporting aids for a human — they are NOT
    # execution rules, are not part of the frozen baseline, and changing them
    # changes no trade.
    #
    # min_score        : only alert setups at or above this NORMALISED score.
    #                    Because real qualified scores cluster high, pick a value
    #                    near the top of the observed range (e.g. 70) or the
    #                    list will not actually be selective.
    # max_per_window   : at most this many alerts per symbol within window_seconds.
    # window_seconds   : rolling time window for the per-symbol cap (seconds).
    # qualify_only     : only alert setups that reached QUALIFIED.
    min_score: int = 70
    max_per_window: int = 3
    window_seconds: int = 30 * 24 * 3600  # 30 days

    # A setup is alertable once it has qualified (or was resolved while still
    # at that stage). Never alert mere detections.
    qualifying_states: Tuple[str, ...] = (
        "QUALIFIED",
        "COMPLETED",
        "QUALIFIED_NO_TRADE",
    )

    def allows(self, state: str, normalized_score: int) -> bool:
        """Whether a setup in the given state/score may be alerted."""
        if state not in self.qualifying_states:
            return False
        return normalized_score >= self.min_score