"""Result-postback: turn a real closed paper trade into social posts.

There are two independent output paths:

* **X (Twitter) ready-post** - zero cost and zero API. Each time a live paper
  trade closes, the module drafts a fully written, attention-catching X post
  (with the result, the running efficiency score and the invite link), saves it
  to ``data/postback/ready_x_<n>.txt`` and pings the operator on Telegram so
  they can copy-paste it. No bot flags, no API fees, full X reach.

* **Bluesky auto-post** - fully automatic via the free public API. Uses only
  stdlib ``urllib`` (matching the rest of the app). Requires a handle and an
  app password; when enabled, each closed trade is posted automatically.

Both paths are gated by config in ``Settings``:

* ``channel_invite_url``        - the public invite link appended to posts.
* ``postback_x_enabled``        - draft + ping Telegram for each close.
* ``postback_bluesky_enabled``  - auto-post to Bluesky.
* ``bluesky_handle``            - e.g. ``mydid.bsky.social``.
* ``bluesky_app_password``      - an App Password, never the account login.

Honesty invariant: every number in a generated post comes from an actually
settled paper trade in the ledger plus the current efficiency summary. No
fabrication, no forward-looking claims dressed up as results.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from ..config import get_settings

logger = logging.getLogger("veyra.marketing")

# --- endpoint used by the Bluesky auto-poster --------------------------------
_BSKY_HOST = "https://bsky.social"
_BSKY_SESSION = _BSKY_HOST + "/xrpc/com.atproto.server.createSession"
_BSKY_CREATE = _BSKY_HOST + "/xrpc/com.atproto.repo.createRecord"

_SIDE_EMOJI = {"LONG": "🟢", "SHORT": "🔴"}
_REASON_LABEL = {"stop": "stopped out", "horizon": "closed at 24h horizon"}


def _fmt_pct(x):
    if x is None:
        return None
    return f"{x * 100:+.1f}%"


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def _invite(settings) -> str:
    url = (settings.channel_invite_url or "").strip()
    return f"\n\nJoin the channel \u2192 {url}" if url else ""


# --- copy generation ---------------------------------------------------------


def generate_x_post(trade: dict, eff: dict, settings=None) -> str:
    """Build a ready-to-paste X post from one closed paper trade.

    Two shorts: a hooking header line, then a proof/CTA line. Honest - every
    figure comes from ``trade``/``eff`` or is omitted.
    """
    settings = settings or get_settings()
    side_emoji = _SIDE_EMOJI.get(str(trade.get("side")).upper(), "")
    symbol = trade.get("symbol", "")
    tf = trade.get("timeframe", "")
    net = _fmt_pct(trade.get("net_return"))
    exit_reason = str(trade.get("exit_reason") or "")
    reason_label = _REASON_LABEL.get(exit_reason.lower(), exit_reason.lower())

    eff_score = eff.get("score")
    eff_live = eff.get("live_sample") or 0

    # Dollar P&L from the real settled trade (never an estimate).
    pnl = trade.get("pnl")
    pnl_txt = ""
    if pnl is not None:
        pnl_txt = f"{pnl:+.2f}$"

    # Header - the attention hook.
    header_parts = [side_emoji, f"{symbol} {tf}", pnl_txt]
    if net:
        header_parts.append(f"({net})")
    header = f"{' '.join(p for p in header_parts if p)} \u2014 {reason_label}."

    lines = [header]
    if eff_live > 0 and eff_score is not None:
        trade_word = "trade" if eff_live == 1 else "trades"
        lines.append(
            f"Efficiency {eff_score:.0f}/100 across {eff_live} closed "
            f"{trade_word}. No cherry-picking."
        )
    else:
        # No live sample yet: do not invent an efficiency number.
        lines.append("Live paper trade settled. Efficiency number still building sample.")
    lines.append("No auto-trading. Just honest, self-graded setups.")
    lines.append(_invite(settings))
    return "\n".join(lines).strip()


def generate_bluesky_post(trade: dict, eff: dict, settings=None) -> str:
    """Build the body text for an auto-posted Bluesky record (one short post)."""
    settings = settings or get_settings()
    body = generate_x_post(trade, eff, settings)
    return body


def generate_telegram_post(trade: dict, eff: dict, settings=None) -> str:
    """Detailed result post for the Telegram channel (trader-grade replay).

    Unlike the short X/Bluesky post, this one shows the full trade anatomy:
    entry/exit/stop, hold time, notional, fees, projected vs realized, and the
    efficiency context - all from the settled ledger row. Nothing invented.
    """
    settings = settings or get_settings()
    side = str(trade.get("side") or "").upper()
    emoji = _SIDE_EMOJI.get(side, "")
    symbol = trade.get("symbol", "")
    tf = trade.get("timeframe", "")
    band = str(trade.get("band") or "SCANNED").title().replace("_", " ")
    reason = str(trade.get("exit_reason") or "horizon").lower()
    reason_label = _REASON_LABEL.get(reason, reason.title())

    entry = trade.get("entry_price")
    exit_p = trade.get("exit_price")
    stop = trade.get("stop_price")
    notional = trade.get("notional") or 1000.0
    net = trade.get("net_return")
    gross = trade.get("gross_return")
    fees = trade.get("fees") or 0.0
    pnl = trade.get("pnl")
    proj = trade.get("projected_return")
    ent_ts = trade.get("entry_ts") or 0
    ext_ts = trade.get("exit_ts") or 0

    hold_h = (ext_ts - ent_ts) / 3600.0 if ext_ts and ent_ts and ext_ts > ent_ts else None

    eff_score = eff.get("score")
    eff_live = eff.get("live_sample") or 0

    lines = [
        f"\u2705 SETUP CLOSED",
        f"{emoji} {symbol} {tf} \u00B7 {side} {band}",
        f"{'=' * 40}",
    ]
    lines.append(f"Reason   : {reason_label}")
    if entry is not None:
        lines.append(f"Entry    : {entry:,.2f}")
    if exit_p is not None:
        lines.append(f"Exit     : {exit_p:,.2f}")
    if stop is not None:
        lines.append(f"Stop     : {stop:,.2f}")
    if entry is not None and exit_p is not None and stop:
        rr = (exit_p - entry) / (entry - stop) if entry - stop else None
        if rr is not None:
            lines.append(f"R:R      : {rr:.2f}")
    if hold_h is not None:
        lines.append(f"Hold     : {hold_h:.0f}h")
    lines.append(f"Size     : ${notional:,.0f}")
    if net is not None and pnl is not None:
        lines.append(f"Net      : {net * 100:+.2f}%  ({pnl:+.2f}$)")
    elif net is not None:
        lines.append(f"Net      : {net * 100:+.2f}%")
    if gross is not None and net is not None:
        lines.append(
            f"Costs    : {abs(gross * 100 - net * 100):.2f}% round-trip (${fees:,.2f})"
        )
    if proj is not None and net is not None:
        lines.append(f"Projected: {proj * 100:+.2f}% \u2192 realized {net * 100:+.2f}%")
    lines.append(f"{'=' * 40}")
    if eff_live > 0 and eff_score is not None:
        trade_word = "trade" if eff_live == 1 else "trades"
        lines.append(
            f"Running efficiency: {eff_score:.0f}/100 across {eff_live} closed "
            f"{trade_word}."
        )
    else:
        lines.append("Efficiency still building live sample.")
    lines.append("No auto-trading. Every number is a real settled ledger draw.")
    return "\n".join(lines).strip()


# --- outputs -----------------------------------------------------------------


def _postback_dir(settings) -> Path:
    d = settings.absolute_data_dir / "postback"
    d.mkdir(parents=True, exist_ok=True)
    return d


class PostBacker:
    """Detects new closed trades, drafts an X post and auto-posts to Bluesky.

    Uses the paper ledger's own ``trades`` list downstream: callers pass the
    trade rows that were newly appended during a checkpoint (see runner wiring)
    so we never double-post. A local state file records already-published
    trades so crashes/restarts cannot cause duplicates.
    """

    def __init__(self, settings=None, telegram_notifier=None):
        self._settings = settings or get_settings()
        self._dir = _postback_dir(self._settings)
        self._state_path = self._dir / "published.json"
        self._state = self._load_state()
        self._tg = telegram_notifier

    # -- state ----------------------------------------------------------------
    def _load_state(self) -> set:
        if self._state_path.exists():
            try:
                raw = json.loads(self._state_path.read_text(encoding="utf-8"))
                return set(raw.get("published", []))
            except (json.JSONDecodeError, OSError):
                logger.warning("postback state unreadable; starting fresh")
        return set()

    def _save_state(self) -> None:
        try:
            tmp = self._state_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({"published": sorted(self._state)}),
                encoding="utf-8",
            )
            tmp.replace(self._state_path)
        except OSError as exc:  # pragma: no cover - best effort
            logger.warning("could not persist postback state: %s", exc)

    @staticmethod
    def _trade_id(t: dict) -> str:
        return f"{t.get('symbol')}|{t.get('timeframe')}|{t.get('exit_ts')}|{t.get('exit_price')}"

    # -- public ---------------------------------------------------------------
    def handle_closed(self, symbol: str, timeframe: str, closed: list) -> int:
        """Publish each newly-closed trade. Returns how many were published."""
        published_count = 0
        for t in closed:
            tid = self._trade_id(t)
            if tid in self._state:
                continue
            eff = _efficiency_for(symbol, timeframe, self._settings)
            _save_snapshot(self._settings, symbol, timeframe, eff)
            x_post = generate_x_post(t, eff, self._settings)
            if self._settings.postback_x_enabled:
                self._write_draft(x_post, t)
            if self._settings.postback_telegram_enabled:
                self._send_telegram(generate_telegram_post(t, eff, self._settings))
            if self._settings.postback_bluesky_enabled:
                self._post_bluesky(generate_bluesky_post(t, eff, self._settings))
            self._state.add(tid)
            published_count += 1
            logger.info("postback published trade %s", tid)
        if published_count:
            self._save_state()
        return published_count

    # -- Telegram -------------------------------------------------------------
    def _send_telegram(self, text: str) -> None:
        """Post the closed-trade result to the Telegram channel."""
        tg = self._tg
        if tg is None:
            from ..notify import TelegramNotifier

            tg = TelegramNotifier(
                bot_token=self._settings.telegram_bot_token or "",
                chat_id=self._settings.telegram_chat_id or "",
                dry_run=False,
                require_entitlement=self._settings.telegram_require_subscription,
                entitled=True,
            )
        try:
            result = tg.send(text)
            if result.get("sent"):
                logger.info("TG result post sent (chat=%s)", result.get("chat_id"))
            elif result.get("dry_run"):
                logger.info("[dry-run] TG result post logged")
            else:
                logger.warning("TG result post not sent: %s", result.get("error"))
        except Exception as exc:  # noqa: BLE001 - never break postback
            logger.warning("TG result post failed: %s", exc)

    # -- X ready-post ---------------------------------------------------------
    def _write_draft(self, text: str, t: dict) -> None:
        """Save a numbered notepad file for manual copy-paste to X."""
        # Find next sequence number by scanning existing files.
        existing = list(self._dir.glob("*.txt"))
        nums = [int(f.stem.split("_")[0]) for f in existing if f.stem.split("_")[0].isdigit()]
        next_num = max(nums, default=0) + 1

        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        sym_safe = t.get("symbol", "UNK").replace("/", "_")
        tf = t.get("timeframe", "?")
        side = t.get("side", "?")
        filename = f"{next_num:03d}_{ts}_{sym_safe}_{tf}_{side}.txt"
        path = self._dir / filename

        header = (
            f"VEYRA POST #{next_num:03d}\n"
            f"Date/Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Market: {t.get('symbol')} {t.get('timeframe')} {t.get('side')}\n"
            f"Net return: {_fmt_pct(t.get('net_return'))}\n"
            f"Exit reason: {t.get('exit_reason')}\n"
            f"{'=' * 60}\n\n"
        )
        try:
            path.write_text(header + text, encoding="utf-8")
            logger.info("X draft saved -> %s", path)
        except OSError as exc:  # pragma: no cover
            logger.warning("could not write X draft: %s", exc)

    # -- Bluesky auto-post ----------------------------------------------------
    def _post_bluesky(self, text: str) -> None:
        handle = (self._settings.bluesky_handle or "").strip()
        app_pw = (self._settings.bluesky_app_password or "").strip()
        if not handle or not app_pw:
            logger.warning("Bluesky enabled but handle/app-password unset; skipping")
            return
        try:
            _bluesky_post(handle, app_pw, text)
            logger.info("Bluesky post sent (handle=%s)", handle)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Bluesky post failed: %s", exc)


# --- helpers -----------------------------------------------------------------


def _efficiency_for(symbol: str, timeframe: str, settings) -> dict:
    from ..efficiency import efficiency_for_market

    snap = _load_snapshot(settings, symbol, timeframe)
    if snap:
        return dict(snap)
    try:
        from ..alerts.live import LiveScan

        live = LiveScan(settings).live(symbol, timeframe, refresh=False)
        score = int(live.get("overall_score") or 0)
        eff = efficiency_for_market(symbol, timeframe, score, settings)
        eff["live_sample"] = eff.get("live_sample") or 0
        return eff
    except Exception:  # noqa: BLE001 - best effort
        return {"score": None, "live_sample": 0, "win_rate": None}


def _snapshot_path(settings) -> Path:
    """Per-market efficiency frozen at trade close (stable across channels)."""
    return _postback_dir(settings) / "efficiency.json"


def _load_snapshot(settings, symbol: str, timeframe: str) -> dict | None:
    p = _snapshot_path(settings)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        snap = raw.get(f"{symbol}|{timeframe}")
        if not snap or snap.get("score") is None:
            return None
        return snap
    except (json.JSONDecodeError, OSError):
        return None


def _save_snapshot(settings, symbol: str, timeframe: str, eff: dict) -> None:
    if not eff or eff.get("score") is None:
        return
    try:
        p = _snapshot_path(settings)
        raw: dict = {}
        if p.exists():
            try:
                raw = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                raw = {}
        raw[f"{symbol}|{timeframe}"] = {
            "score": eff.get("score"),
            "live_sample": eff.get("live_sample") or 0,
            "win_rate": eff.get("win_rate"),
            "frozen_at": int(datetime.now().timestamp()),
        }
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(raw, sort_keys=True, indent=2), encoding="utf-8")
        tmp.replace(p)
    except OSError:  # pragma: no cover - best effort
        pass


def _bluesky_post(handle: str, app_password: str, text: str) -> dict:
    """Create a post on Bluesky using an App Password (stdlib only)."""
    auth = _json_request(_BSKY_SESSION, {"identifier": handle, "password": app_password})
    access_jwt = auth["accessJwt"]
    did = auth["did"]
    body = {
        "repo": did,
        "collection": "app.bsky.feed.post",
        "record": {
            "$type": "app.bsky.feed.post",
            "text": text,
            "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        },
    }
    return _json_request(_BSKY_CREATE, body, headers={"Authorization": f"Bearer {access_jwt}"})


def _json_request(url: str, payload: dict, headers: dict | None = None) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "veyra/postback/1.0",
            **(headers or {}),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 - HTTPS
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw)
