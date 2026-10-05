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
                emitted.append(self._live_row(sym, tf, a, state, market=m))
        self._save()
        return emitted

    @staticmethod
    def _live_row(sym: str, tf: str, a: dict, state: str, market: dict | None = None) -> dict:
        zone = a.get("interest_area") or {}
        low = zone.get("low") if isinstance(zone, dict) else None
        high = zone.get("high") if isinstance(zone, dict) else None
        ts = a.get("timestamp")
        # Stable pairing ID for LIVE SETUP -> SETUP CLOSED matching.
        # Dedupe key stays type|side (no spam); this ID is display-only.
        setup_type = str(a.get("setup_type", "?"))
        side = str(a.get("side", "?"))
        alert_id = f"{str(sym).replace('/', '')}-{tf}-{setup_type}-{side}-{ts or 'live'}"
        mkt = market or {}
        return {
            "symbol": sym,
            "timeframe": tf,
            "setup_key": f"{setup_type}|{side}",
            "alert_id": alert_id,
            "setup_type": setup_type,
            "side": side,
            "level": "",
            "score": int(a.get("overall_score") or 0),
            "state": state,
            "interest_area_low": low,
            "interest_area_high": high,
            "invalidation": a.get("invalidation") or "n/a",
            "reasoning": a.get("reasoning") or a.get("setup_type") or "",
            "timestamp": ts,
            "atr": a.get("atr"),
            "regime": a.get("regime") or mkt.get("regime") or "",
            "live_price": mkt.get("live_price"),
        }

    @staticmethod
    def _row(sym: str, tf: str, a: dict, state: str) -> dict:
        zone = a.get("interest_area_low"), a.get("interest_area_high")
        invalidation = a.get("invalidation") or "n/a"
        reason = (a.get("reasoning") or a.get("setup_type") or "").strip()
        key = str(a.get("setup_key", a.get("alert_id", "")))
        return {
            "symbol": sym,
            "timeframe": tf,
            "setup_key": key,
            "alert_id": str(a.get("alert_id") or key or f"{sym}-{tf}-{state}"),
            "setup_type": a.get("setup_type", ""),
            "side": a.get("side", ""),
            "level": a.get("level", ""),
            "score": int(a.get("score_normalized") or a.get("overall_score") or 0),
            "state": state,
            "interest_area_low": zone[0],
            "interest_area_high": zone[1],
            "invalidation": invalidation,
            "reasoning": reason,
            "timestamp": a.get("timestamp"),
            "atr": a.get("atr"),
            "regime": a.get("regime") or "",
            "live_price": a.get("live_price"),
        }


def _parse_float(value) -> float | None:
    """Best-effort float parse for invalidation levels (None when text)."""
    try:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return float(str(value).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None


def render_message(row: dict, projection: Optional[dict] = None,
                   amount: Optional[float] = None,
                   timeframe: Optional[str] = None,
                   efficiency: Optional[dict] = None,
                   track_url: Optional[str] = None,
                   risk_usd: float = 10.0,
                   decision: Optional[dict] = None) -> str:
    """Render a Telegram alert from a clerk row (HTML for Telegram's parse_mode).

    Live Binance observation - informational only, never an order. It
    describes what the live engine detected plus the evidence-weighted edge, in
    a clean layout designed to look professional - it never promises a
    guaranteed profit or routes a real order.

    Output uses Telegram's safe HTML subset: `<b>`, `<i>`, `<code>`, `<pre>`,
    lists and unordered spans. When ``projection`` is provided it appends the
    band's realized track record; when ``efficiency`` is provided it appends
    the live 0-100 efficiency score (adaptive as real trades complete).
    When ``decision`` (from veyra.decision.decide) is provided it renders the
    quantified verdict banner first: TRADE / WATCH / STAND_ASIDE with R:R,
    sample, expectancy and the blocking reason — the evidence layer that
    makes the call worth considering (or explicitly not).
    """
    from html import escape as _esc  # local, only used for rendering output

    sym = _esc(str(row.get("symbol", "?")))
    tf_raw = str(row.get("timeframe", "?"))
    tf = _esc(tf_raw)
    side_raw = str(row.get("side", "?")).upper()
    side = _esc(str(row.get("side", "?")))
    setup_type = _esc(str(row.get("setup_type", "?")))
    state = _esc(str(row.get("state", "?")))
    tf_arg = timeframe or tf_raw

    low = _parse_float(row.get("interest_area_low"))
    high = _parse_float(row.get("interest_area_high"))
    zone = None
    if low is not None and high is not None:
        zone = (f"${low:,.1f} \u2013 "
                f"${high:,.1f}")

    side_emoji = {"LONG": "\U0001F7E2", "SHORT": "\U0001F534", "BUY": "\U0001F7E2",
                  "SELL": "\U0001F534"}.get(side_raw, "\u25B6\U0000FE0F")

    lines = []
    # Header bar - the pair-marker: traders see LIVE SETUP now, then the
    # matched SETUP CLOSED receipt later (same sym/tf/side/setup_type).
    lines.append(f"<b>{side_emoji} LIVE SETUP</b>")
    lines.append(f"<b>{sym} \u00B7 {tf}</b>  <b>{side}</b> {setup_type}")
    lines.append(
        f"<code>Status: {state} \u00B7 score {row.get('score','?')}/100</code>"
    )
    lines.append("<i>Score = quality rank, not win probability.</i>")
    # Quantified verdict banner — the evidence layer that makes this worth
    # considering (or explicitly not). Rendered first so a weak setup can
    # never be mistaken for a recommendation.
    _dec = decision or row.get("decision")
    if isinstance(_dec, dict) and _dec.get("verdict"):
        _v = str(_dec.get("verdict")).upper()
        _n = _dec.get("n") or 0
        _wr = _dec.get("win_rate")
        _exp = _dec.get("expectancy")
        _rr = _dec.get("rr")
        _es = _dec.get("efficiency_score")
        if _v == "TRADE":
            lines.append("<b>Verdict: TRADE — worth considering</b>")
        elif _v == "WATCH":
            lines.append("<b>Verdict: WATCH — monitor, do not size</b>")
        else:
            lines.append("<b>Verdict: STAND ASIDE — no edge on current evidence</b>")
        _bits = []
        if _n:
            _bits.append(f"n={int(_n)}")
        if _wr is not None:
            try:
                _bits.append(f"win {float(_wr)*100:.0f}%")
            except (TypeError, ValueError):
                pass
        if _exp is not None:
            try:
                _bits.append(f"exp {float(_exp)*100:+.2f}%/trade")
            except (TypeError, ValueError):
                pass
        if _rr is not None:
            try:
                _bits.append(f"R:R {float(_rr):.2f}")
            except (TypeError, ValueError):
                pass
        if _es is not None:
            _bits.append(f"eff {_es}/100")
        if _bits:
            lines.append("<code>" + _esc(" · ".join(_bits)) + "</code>")
        _why = "; ".join(str(x) for x in (_dec.get("reasons") or []) if x)
        if _why and _v != "TRADE":
            lines.append(f"<i>Why: {_esc(_why)}</i>")
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

    # Trigger - explicit action cue so users stop guessing entries.
    if low is not None and high is not None:
        if side_raw in ("LONG", "BUY"):
            lines.append(f"\u2022 <b>Trigger:</b> wait for {tf_raw} close holding above "
                         f"${high:,.1f}; entry next open. No chase &gt;1% above zone.")
        elif side_raw in ("SHORT", "SELL"):
            lines.append(f"\u2022 <b>Trigger:</b> wait for {tf_raw} close holding below "
                         f"${low:,.1f}; entry next open. No chase &gt;1% below zone.")
        else:
            lines.append(f"\u2022 <b>Trigger:</b> next {tf_raw} open after {state}; confirm on close.")
    else:
        lines.append(f"\u2022 <b>Trigger:</b> next {tf_raw} open after {state}; confirm on close.")
    live_px = _parse_float(row.get("live_price"))
    if live_px is not None:
        lines.append(f"\u2022 <b>Live price:</b> ${live_px:,.1f}")
    lines.append("")

    # Risk box - fixed-budget sizing, never an order.
    cap = float(amount) if amount else 1000.0
    entry_est = (low + high) / 2.0 if (low is not None and high is not None and high > 0) else None
    stop_px = _parse_float(inval)
    atr = _parse_float(row.get("atr"))
    stop_pct: float | None = None
    stop_label = ""
    if entry_est and stop_px and stop_px > 0:
        if (side_raw in ("LONG", "BUY") and stop_px < entry_est) or \
           (side_raw in ("SHORT", "SELL") and stop_px > entry_est):
            stop_pct = abs(entry_est - stop_px) / entry_est
            stop_label = f"${stop_px:,.1f}"
    if stop_pct is None and entry_est and atr and atr > 0:
        # Fallback mirrors paper engine: stop = 2x ATR from entry estimate.
        stop_pct = (atr * 2.0) / entry_est
        stop_label = f"~${entry_est - atr * 2.0:,.1f} (2x ATR)" if side_raw in ("LONG", "BUY") \
            else f"~${entry_est + atr * 2.0:,.1f} (2x ATR)"
    lines.append("<b>Risk \u00B7 position guide (not an order)</b>")
    if stop_pct and stop_pct > 1e-6:
        size = min(cap, float(risk_usd) / stop_pct)
        lines.append(f"\u2022 Stop {stop_label} ({stop_pct*100:.2f}%) \u00B7 risk "
                     f"${float(risk_usd):,.0f} \u00B7 size ~${size:,.0f} of ${cap:,.0f} cap")
    else:
        lines.append(f"\u2022 Risk ${float(risk_usd):,.0f}/trade \u00B7 size up to ${cap:,.0f} "
                     f"\u00B7 stop = invalidation")
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

    rr_shown = False
    if projection and projection.get("n"):
        n = int(projection["n"])
        mr = projection.get("mean_return")
        risk = projection.get("risk")
        est_pnl = None
        if mr is not None and amount:
            est_pnl = float(amount) * float(mr)
        if est_pnl is not None:
            signs = "+" if est_pnl >= 0 else "-"
            lines.append(f"\u2022 <b>Est. 24h P&L @ ${float(amount):,.0f}:</b> "
                         f"{signs}${abs(est_pnl):,.0f} (n={n})")
        elif mr is not None:
            lines.append(f"\u2022 <b>Est. 24h return:</b> {float(mr)*100:+.2f}% (n={n})")
        else:
            lines.append(f"\u2022 Track record n={n}")
        if risk is not None and mr is not None:
            try:
                mr_f, risk_f = float(mr), float(risk)
                edge = "edged" if abs(mr_f) > abs(risk_f) else "not edged"
                lines.append(f"\u2022 Upside {abs(mr_f)*100:.2f}% vs downside "
                             f"{abs(risk_f)*100:.2f}% ({edge})")
                if risk_f > 1e-9:
                    lines.append(f"\u2022 <b>Est. R:R ~{abs(mr_f)/abs(risk_f):.2f}</b> "
                                 f"(reward vs downside)")
                    rr_shown = True
            except (TypeError, ValueError):
                pass
        if tf_arg:
            horizon_txt = {"4H": "next 24h / ~4 bars",
                           "1D": "next 24h / ~1 day",
                           "15m": "next few hours",
                           "1H": "next 24h / ~24 bars"}.get(tf_arg, "next 24h")
            lines.append(f"\u2022 <b>Window:</b> live until invalidation \u00B7 "
                         f"projection {horizon_txt}")
    if not rr_shown and stop_pct and stop_pct > 1e-6 and entry_est and low is not None and high is not None:
        # Structural fallback R:R so entry always shows one honest number.
        zone_h = abs(high - low) / entry_est if entry_est else 0
        if zone_h > 0:
            lines.append(f"\u2022 <b>Struct. R:R ~{zone_h/stop_pct:.2f}</b> (zone height vs stop)")

    lines.append("")
    alert_id = row.get("alert_id") or row.get("setup_key") or "n/a"
    lines.append(f"<code>ID: {_esc(str(alert_id))}</code>")
    if track_url:
        lines.append(f"Track record: {_esc(str(track_url))}")
    else:
        lines.append("Track: full win+loss ledger in pinned post")
    lines.append("<i>Live Binance observation \u2014 informational only, not an "
                 "order. Never a guarantee \u2014 always manage your own risk.</i>")
    return "\n".join(lines)