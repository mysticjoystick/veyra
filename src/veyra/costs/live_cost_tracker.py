"""Live cost tracking — realized vs assumed execution costs.

The whole system's ``net_return`` math uses the fixed assumption
``round_trip = 2 x (0.001 fee + 0.0005 slippage) = 0.003``. If real fills drift
away from that constant, every projected and realized number quietly degrades.

This tracker appends one JSONL record per closed paper trade with what the
*model* assumed vs what the fill made us pay, then surfaces a week-over-week
drift report. In the current paper sim the fill IS the model (no broker), so
``realized_cost == assumed`` and ``fill_slippage == 0.0`` by construction; the
record keeps the schema + drift-alarm pipeline warm so the moment a real
execution feed exists it starts policing the assumption instead of decaying.

Drift alert rule: the trailing average realized cost must exceed the assumption
by more than ``alert_pct`` for ``alert_weeks`` consecutive elapsed UTC weeks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from ..alerts.costs import CostModel


@dataclass
class CostRecord:
    symbol: str
    timeframe: str
    closed_at: int          # epoch seconds (UTC) of the close
    notional: float
    assumed_round_trip: float
    realized_cost: float    # total fees+slippage as a fraction of notional
    expected_entry: Optional[float]
    entry_price: float
    fill_slippage: float    # (entry_price - expected_entry) / expected_entry

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "closed_at": self.closed_at,
            "notional": self.notional,
            "assumed_round_trip": self.assumed_round_trip,
            "realized_cost": self.realized_cost,
            "expected_entry": self.expected_entry,
            "entry_price": self.entry_price,
            "fill_slippage": self.fill_slippage,
        }


class LiveCostTracker:
    def __init__(
        self,
        path: Path,
        *,
        assumed_round_trip: Optional[float] = None,
        alert_pct: float = 0.20,
        alert_weeks: int = 2,
    ) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._assumed = (
            float(assumed_round_trip)
            if assumed_round_trip is not None
            else CostModel().round_trip
        )
        self._alert_pct = float(alert_pct)
        self._alert_weeks = int(alert_weeks)

    # -- recording --------------------------------------------------------

    def record(
        self,
        symbol: str,
        timeframe: str,
        position,
        expected_entry: Optional[float] = None,
    ) -> CostRecord:
        """Append one record for a closed position.

        The paper sim realizes exactly what it modeled: fees+slippage are the
        position's own ``fees``/``notional`` (equal to the assumption), and the
        fill is the position's entry price (expected == actual -> zero drift).
        """
        notional = float(getattr(position, "notional") or 0.0) or 1.0
        fees = float(getattr(position, "fees") or 0.0)
        entry = float(getattr(position, "entry_price") or 0.0)
        realized = fees / notional if notional else self._assumed
        expected = float(expected_entry) if expected_entry is not None else entry
        slippage = ((entry - expected) / expected) if expected else 0.0
        record = CostRecord(
            symbol=symbol,
            timeframe=timeframe,
            closed_at=int(getattr(position, "exit_ts") or 0),
            notional=notional,
            assumed_round_trip=self._assumed,
            realized_cost=realized,
            expected_entry=expected,
            entry_price=entry,
            fill_slippage=slippage,
        )
        self._append(record.to_dict())
        return record

    def _append(self, data: dict) -> None:
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(data) + "\n")

    # -- reporting --------------------------------------------------------

    def _rows(self) -> List[CostRecord]:
        if not self._path.exists():
            return []
        rows = []
        with self._path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(CostRecord(**json.loads(line)))
                except (TypeError, ValueError):
                    continue  # skip corrupted rows; never fault the report
        return rows

    def report(self, rows: Optional[List[CostRecord]] = None) -> dict:
        rows = rows if rows is not None else self._rows()
        if not rows:
            return {
                "n_trades": 0,
                "assumed_round_trip": self._assumed,
                "avg_realized_cost": None,
                "avg_fill_slippage": None,
                "weeks": [],
                "alert": None,
            }
        avg_realized = sum(r.realized_cost for r in rows) / len(rows)
        avg_slip = sum(r.fill_slippage for r in rows) / len(rows)
        return {
            "n_trades": len(rows),
            "assumed_round_trip": self._assumed,
            "avg_realized_cost": avg_realized,
            "avg_fill_slippage": avg_slip,
            "weeks": self._weekly_summary(rows),
            "alert": self.check_drift(),
        }

    def _weekly_summary(self, rows: List[CostRecord]) -> List[dict]:
        import time
        from collections import OrderedDict

        weeks: Dict[str, List[CostRecord]] = OrderedDict()
        for row in rows:
            key = time.strftime("%Y-W%W", time.gmtime(row.closed_at))
            weeks.setdefault(key, []).append(row)
        return [
            {"week": key, "trades": len(v),
             "avg_realized_cost": sum(r.realized_cost for r in v) / len(v)}
            for key, v in weeks.items()
        ]

    def check_drift(self, rows: Optional[List[CostRecord]] = None) -> Optional[dict]:
        """Return an alert when realized cost has exceeded the assumption by more
        than ``alert_pct`` for ``alert_weeks`` consecutive elapsed weeks."""
        rows = rows if rows is not None else self._rows()
        if not rows:
            return None
        summary = self._weekly_summary(rows)
        if len(summary) < self._alert_weeks:
            return None
        thresholds_met = 0
        for wk in reversed(summary):  # most recent week first
            avg = wk["avg_realized_cost"]
            if avg >= self._assumed * (1.0 + self._alert_pct):
                thresholds_met += 1
            else:
                break
        if thresholds_met >= self._alert_weeks:
            return {
                "level": "warning",
                "message": (
                    f"realized cost {summary[-1]['avg_realized_cost']:.6f} exceeds "
                    f"assumed {self._assumed:.6f} by >{self._alert_pct:.0%} for "
                    f"{thresholds_met} consecutive weeks — the fixed 0.3% "
                    "round-trip constant may be stale."
                ),
                "assumed_round_trip": self._assumed,
                "consecutive_weeks": thresholds_met,
            }
        return None