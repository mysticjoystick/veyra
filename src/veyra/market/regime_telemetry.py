"""Regime-transition telemetry.

Regime is a per-market property that flips discretely (BULL -> RANGE ->
HIGH_VOLATILITY ...). Each flip is a first-order signal for strategy and risk
routing, so we persist every transition rather than only the current value:

    { "symbol": "BTC/USDT", "timeframe": "4H", "from": "BULL", "to": "RANGE",
      "ts": 1789000000, "evidence": {...triggering lens values...} }

Only *transitions* are recorded (same regime twice in a row is a no-op), keyed
by symbol|timeframe, and the in-memory state is rehydrated from the file on
start so restarts don't cause phantom transitions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional


class RegimeTelemetry:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._last: Dict[str, str] = {}
        self._rehydrate()

    # -- persistence ------------------------------------------------------

    def _rehydrate(self) -> None:
        if not self._path.exists():
            return
        try:
            with self._path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    key = self._key(row.get("symbol", ""), row.get("timeframe", ""))
                    self._last[key] = row.get("to")
        except OSError:
            pass

    def _key(self, symbol: str, timeframe: str) -> str:
        return f"{symbol}|{timeframe}"

    def transitions(self, symbol: Optional[str] = None,
                    timeframe: Optional[str] = None) -> List[dict]:
        if not self._path.exists():
            return []
        rows = []
        with self._path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if symbol and row.get("symbol") != symbol:
                    continue
                if timeframe and row.get("timeframe") != timeframe:
                    continue
                rows.append(row)
        return rows

    def last_regime(self, symbol: str, timeframe: str) -> Optional[str]:
        return self._last.get(self._key(symbol, timeframe))

    # -- recording --------------------------------------------------------

    def observe(self, symbol: str, timeframe: str, regime: str,
                evidence: Optional[dict] = None, ts: Optional[int] = None) -> Optional[dict]:
        """Record a regime change for a market; no-op when regime is unchanged.

        Returns the transition row when a change was persisted, else None.
        """
        import time

        key = self._key(symbol, timeframe)
        ts = int(ts) if ts is not None else int(time.time())
        previous = self._last.get(key)
        if previous == regime:
            return None

        row = {
            "symbol": symbol,
            "timeframe": timeframe,
            "from": previous,  # None on first observation (no transition yet)
            "to": regime,
            "ts": ts,
            "evidence": dict(evidence or {}),
        }
        # First observation for a market initialises state without a file row:
        # there is no *change* to report yet.
        if previous is None:
            self._last[key] = regime
            return None

        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")
        self._last[key] = regime
        return row