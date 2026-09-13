"""Idempotent, honest state-change notifier.

The clerk turns an alert snapshot into notifications *only when an alert's
lifecycle state actually changes* between runs. It keeps the last-seen state
per alert on disk, so repeated runs with no changes produce no messages.

This matters for honesty: if nothing changed, nothing is sent — the clerk never
broadcasts the same alert twice, and never invents a state.

State model (mirrors the setup lifecycle in the engine):
  DETECTED -> DEVELOPING -> QUALIFIED -> TRIGGERED
                               |
                               +--------> INVALIDATED / EXPIRED / COMPLETED
Unknown/absent -> DETECTED on first observation.

All state here is SIMULATION ONLY. Notifications describe what the engine
detected; they are notices, never orders.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

STATE = ["DETECTED", "DEVELOPING", "QUALIFIED", "TRIGGERED", "INVALIDATED", "EXPIRED", "COMPLETED"]
RANK = {s: i for i, s in enumerate(STATE)}

# Forward-progress arrows for messages (render as-is, no fancy glyphs).
_ARROW = "->"


def setup_state_for(stage: Optional[str], level: Optional[str], score: Optional[int]) -> str:
    """Map an alert's payload to a single lifecycle state string.

    A tested setup (its `level` was reported) is considered QUALIFIED when its
    clarity is at or above the DIRECTIONAL floor and it scored high enough;
    otherwise it is DEVELOPING. Stage strings we do not understand map to the
    raw value so nothing is hidden.
    """
    s = (str(stage or "") or "").upper()
    if s in STATE:
        return s
    if s:
        return s
    lvl = str(level or "").upper()
    score = 0 if score is None else int(score)
    if lvl in ("CONVERGENT", "DIRECTIONAL") or score >= 55:
        return "QUALIFIED"
    if lvl in ("EMERGENT",) or score >= 40:
        return "DEVELOPING"
    return "DETECTED"


def _live_state(score: int) -> str:
    """Live setup lifecycle from its clarity score.

    Live setups carry no explicit engine `state` (they are detected on the
    newest live bar), so the clerk derives the lifecycle from the score band:
    >=55 (DIRECTIONAL/CONVERGENT) is QUALIFIED, >=40 is DEVELOPING, otherwise
    DETECTED. Keys persist across bars, so QUALIFIED setups do not re-alert
    every bar — only when the band actually shifts.
    """
    if score >= 55:
        return "QUALIFIED"
    if score >= 40:
        return "DEVELOPING"
    return "DETECTED"


class NotifyClerk:
    """Track per-alert lifecycle state and emit notifications on change."""

    def __init__(
        self,
        state_path: Optional[Path] = None,
        min_change: Optional[str] = None,
        only_states: Optional[List[str]] = None,
    ) -> None:
        self._state_path = Path(state_path or "data/state/notify_state.json")
        self._min_change = (str(min_change or "").upper()) if min_change else ""
        self._only_states = [str(s).upper() for s in (only_states or [])]
        self._state: Dict[str, dict] = self._load()

    # -- persistence ------------------------------------------------------

    def _load(self) -> Dict[str, dict]:
        try:
            if self._state_path.exists():
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("alerts"), dict):
                    return data["alerts"]
        except (OSError, ValueError):  # noqa: BLE001 - a corrupt state file must not crash
            pass
        return {}

    def _save(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps({"alerts": self._state, "saved_at": int(time.time())}, indent=2),
                encoding="utf-8",
            )
        except OSError:  # noqa: BLE001 - persistence failure is non-fatal for a notifier
            pass

    # -- notification logic -----------------------------------------------

    def _key(self, symbol: str, timeframe: str, setup_key: str) -> str:
        return f"{symbol}|{timeframe}|{setup_key}"

    def _passes_filters(self, state: str, score: int) -> bool:
        if self._only_states and state not in self._only_states:
            return False
        if self._min_change and RANK.get(state, 0) < RANK.get(self._min_change, 0):
            return False
        return True

    def _changed(self, key: str, new_state: str) -> bool:
        prev = self._state.get(key, {}).get("state")
        self._state[key] = {
            "state": new_state,
            "first_seen": self._state.get(key, {}).get("first_seen", int(time.time())),
            "updated_at": int(time.time()),
        }
        return prev is None or prev != new_state

    def scan(self, datasets: List[dict]) -> List[dict]:
        """Compute intra-run state and return only the changed, filter-passing rows.

        Each returned row is a dict with the message fields the CLI can render
        and send. Unchanged or filtered rows are simply not returned (and the
        clerk's on-disk state advances for all observed alerts).
        """
        emitted: List[dict] = []
        for ds in datasets:
            sym = ds.get("symbol", "")
            tf = ds.get("timeframe", "")
            for a in ds.get("alerts", []):
                key = self._key(sym, tf, a.get("setup_key", a.get("alert_id", "")))
                new_state = setup_state_for(
                    a.get("outcome") or a.get("_state"),
                    a.get("level"),
                    a.get("score_normalized"),
                )
                score = int(a.get("score_normalized") or a.get("overall_score") or 0)
                if not self._changed(key, new_state):
                    continue
                if not self._passes_filters(new_state, score):
                    continue
                emitted.append(self._row(sym, tf, a, new_state))
        self._save()
        return emitted

    def scan_live(self, live_markets: List[dict], only_states: Optional[List[str]] = None) -> List[dict]:
        """Scan *live* setups (as surfaced by LiveScan, with a real `state`).

        This is the honest Phase 7 path: it notifies only on genuinely live
        setup formation / development (state = DETECTED/DEVELOPING/QUALIFIED/
        TRIGGERED/...), never on historical replay backfill. Live setups that
        have not changed state between runs produce no message.

        Each live setup is keyed by symbol|timeframe|setup_type|side **without
        the bar timestamp** so the same setup that persists across bars is
        tracked as one alert (no spam on every new bar). The state derives from
        the setup's score band (DEVELOPING -> QUALIFIED as clarity climbs), so
        it only fires on a genuine development, and keys that disappear from the
        market are dropped so a later re-appearance alerts again.
        """
        emitted: List[dict] = []
        present = {}
        for m in live_markets:
            sym = m.get("symbol", "")
            tf = m.get("timeframe", "")
            keys = {
                self._key(sym, tf, f"{a.get('setup_type','?')}|{a.get('side','?')}")
                for a in (m.get("setups") or [])
            }
            present[(sym, tf)] = keys

        # Drop state for setups that are no longer present in their market, so a
        # later re-appearance of the same setup alerts again (fresh detection).
        stale = []
        for k in self._state:
            sym, tf, _ = k.split("|", 2)
            market_keys = present.get((sym, tf))
            if market_keys is not None and k not in market_keys:
                stale.append(k)
        for k in stale:
            self._state.pop(k, None)

        for m in live_markets:
            sym = m.get("symbol", "")
            tf = m.get("timeframe", "")
            for a in m.get("setups") or []:
                state = _live_state(int(a.get("overall_score") or 0))
                if only_states and state not in only_states:
                    continue
                key = self._key(sym, tf, f"{a.get('setup_type','?')}|{a.get('side','?')}")
                score = int(a.get("overall_score") or 0)
                if not self._changed(key, state):
                    continue
                if not self._passes_filters(state, score):
                    continue
                emitted.append(self._live_row(sym, tf, a, state))
        self._save()
        return emitted

    @staticmethod
    def _live_row(sym: str, tf: str, a: dict, state: str) -> dict:
        zone = a.get("interest_area") or {}
        low = zone.get("low") if isinstance(zone, dict) else None
        high = zone.get("high") if isinstance(zone, dict) else None
        return {
            "symbol": sym,
            "timeframe": tf,
            "setup_key": f"{a.get('setup_type','?')}|{a.get('side','?')}",
            "setup_type": a.get("setup_type", ""),
            "side": a.get("side", ""),
            "level": "",
            "score": int(a.get("overall_score") or 0),
            "state": state,
            "interest_area_low": low,
            "interest_area_high": high,
            "invalidation": a.get("invalidation") or "n/a",
            "reasoning": a.get("reasoning") or a.get("setup_type") or "",
        }

    @staticmethod
    def _row(sym: str, tf: str, a: dict, state: str) -> dict:
        zone = a.get("interest_area_low"), a.get("interest_area_high")
        invalidation = a.get("invalidation") or "n/a"
        reason = (a.get("reasoning") or a.get("setup_type") or "").strip()
        return {
            "symbol": sym,
            "timeframe": tf,
            "setup_key": a.get("setup_key", a.get("alert_id", "")),
            "setup_type": a.get("setup_type", ""),
            "side": a.get("side", ""),
            "level": a.get("level", ""),
            "score": int(a.get("score_normalized") or a.get("overall_score") or 0),
            "state": state,
            "interest_area_low": zone[0],
            "interest_area_high": zone[1],
            "invalidation": invalidation,
            "reasoning": reason,
        }


def render_message(row: dict, projection: Optional[dict] = None,
                   amount: Optional[float] = None,
                   timeframe: Optional[str] = None,
                   efficiency: Optional[dict] = None) -> str:
    """Render a Telegram alert from a clerk row (HTML for Telegram's parse_mode).

    Live Binance observation - informational only, never an order. It
    describes what the live engine detected plus the evidence-weighted edge, in
    a clean layout designed to look professional - it never promises a
    guaranteed profit or routes a real order.

    Output uses Telegram's safe HTML subset: `<b>`, `<i>`, `<code>`, `<pre>`,
    lists and unordered spans. When ``projection`` is provided it appends the
    band's realized track record; when ``efficiency`` is provided it appends
    the live 0-100 efficiency score (adaptive as real trades complete).
    """
    from html import escape as _esc  # local, only used for rendering output

    sym = _esc(str(row.get("symbol", "?")))
    tf = _esc(str(row.get("timeframe", "?")))
    side = _esc(str(row.get("side", "?")))
    setup_type = _esc(str(row.get("setup_type", "?")))
    state = _esc(str(row.get("state", "?")))

    zone = None
    if row.get("interest_area_low") is not None and row.get("interest_area_high") is not None:
        zone = (f"${float(row['interest_area_low']):,.1f} \u2013 "
                f"${float(row['interest_area_high']):,.1f}")

    side_emoji = {"LONG": "\U0001F7E2", "SHORT": "\U0001F534", "BUY": "\U0001F7E2",
                  "SELL": "\U0001F534"}.get(side.upper(), "\u25B6\U0000FE0F")

    lines = []
    # Header bar - the pair-marker: traders see LIVE SETUP now, then the
    # matched SETUP CLOSED receipt later (same sym/tf/side/setup_type).
    lines.append(f"<b>{side_emoji} LIVE SETUP</b>")
    lines.append(f"<b>{sym} \u00B7 {tf}</b>  <b>{side}</b> {setup_type}")
    lines.append(
        f"<code>Status: {state} \u00B7 score {row.get('score','?')}/100</code>"
    )
    lines.append("")

    # Setup detail
    lines.append("<b>Setup</b>")
    lines.append(f"\u2022 {side} {setup_type} on {sym} {tf}")
    if zone:
        lines.append(f"\u2022 <b>Interest zone:</b> {zone}")
    inval = row.get("invalidation")
    if inval and str(inval).lower() not in ("n/a", ""):
        lines.append(f"\u2022 <b>Invalidates if:</b> {_esc(str(inval))}")
    reason = row.get("reasoning")
    if reason:
        lines.append(f"\u2022 <b>Why:</b> {_esc(str(reason).strip())}")
    lines.append("")

    # Edge / evidence
    lines.append("<b>Edge \u00B7 evidence-weighted</b>")
    if efficiency and efficiency.get("score") is not None:
        sc = int(efficiency["score"])
        wr = efficiency.get("win_rate")
        exp = efficiency.get("expectancy")
        live_n = int(efficiency.get("live_sample") or 0)
        conf = efficiency.get("confidence", "LOW")
        eff_parts = [f"efficiency <b>{sc}/100</b> ({conf})"]
        if wr is not None:
            eff_parts.append(f"win-rate {wr*100:.0f}%")
        if exp is not None:
            eff_parts.append(f"expectancy {exp*100:+.2f}%/trade")
        lines.append("\u2022 " + " \u00B7 ".join(eff_parts))
        if live_n:
            lines.append(f"\u2022 adaptive \u2013 improves with each closed live trade "
                         f"(sample n={live_n})")
    lines.append("\u2022 live Binance observation \u2013 updated on each scan; "
                 "not a once-off")

    if projection and projection.get("n"):
        n = int(projection["n"])
        mr = projection.get("mean_return")
        risk = projection.get("risk")
        est_pnl = None
        if mr is not None and amount:
            est_pnl = amount * mr
        if est_pnl is not None:
            signs = "+" if est_pnl >= 0 else "-"
            lines.append(f"\u2022 <b>Est. 24h P&L @ ${amount:,.0f}:</b> "
                         f"{signs}${abs(est_pnl):,.0f}")
        elif mr is not None:
            lines.append(f"\u2022 <b>Est. 24h return:</b> {mr*100:+.2f}%")
        if risk is not None and mr is not None:
            edge = "edged" if abs(mr) > abs(risk) else "not edged"
            lines.append(f"\u2022 Upside {abs(mr)*100:.2f}% vs downside "
                         f"{abs(risk)*100:.2f}% ({edge})")
        if timeframe:
            horizon_txt = {"4H": "next 24h / ~4 bars",
                           "1D": "next 24h / ~1 day",
                           "15m": "next few hours"}.get(timeframe, "next 24h")
            lines.append(f"\u2022 <b>Window:</b> live until invalidation \u00B7 "
                         f"projection {horizon_txt}")

    lines.append("")
    lines.append("<i>Live Binance observation \u2014 informational only, not an "
                 "order. Never a guarantee \u2014 always manage your own risk.</i>")
    return "\n".join(lines)