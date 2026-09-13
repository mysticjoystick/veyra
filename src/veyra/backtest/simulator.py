"""Simulation state manager.

Tracks live setups, open positions, completed trades, and setup lifecycle
outcomes for the replay engine. Encapsulates the overlap policy so the engine
can focus on chronology.

This module deliberately keeps research results separate from the live
SetupRepository: nothing here mutates live setup state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ..domain import Setup, SetupState
from ..market.pipeline import MarketSnapshot
from . import execution as ex
from .execution import Bar
from .models import (
    BacktestEvent,
    BacktestTrade,
    ExecutionConfig,
    SetupRecord,
    SetupOutcome,
)
from .outcomes import classify_outcome

_TERMINAL = {SetupState.INVALIDATED, SetupState.EXPIRED, SetupState.COMPLETED}


def _is_terminal(state) -> bool:
    return state in _TERMINAL


@dataclass
class OpenPosition:
    pos_key: str
    setup_key: str
    side: str                        # "LONG" | "SHORT"
    entry_price: float               # cost-adjusted fill
    entry_raw: float                 # unadjusted bar open
    stop: float
    target: Optional[float]
    entry_ts: int
    entry_bar: int
    high_water: float
    low_water: float
    _atr: Optional[float] = None     # ATR at entry for trailing stop


@dataclass
class TrackedSetup:
    setup: Setup
    detection_bar: int
    entry_bar: Optional[int] = None
    qualification_ts: Optional[int] = None
    trade_id: Optional[str] = None
    final_state: Optional[str] = None


class Simulator:
    def __init__(self, run_key: str, symbol: str, timeframe: str,
                 config: ExecutionConfig, interval_seconds: int,
                 trailing_atr_multiple: float = 1.0) -> None:
        self._run_key = run_key
        self._symbol = symbol
        self._timeframe = timeframe
        self._config = config
        self._interval = interval_seconds
        self._tracked: Dict[str, TrackedSetup] = {}
        self._positions: Dict[str, OpenPosition] = {}
        self._setups: List[SetupRecord] = []
        self._trades: List[BacktestTrade] = []
        self._events: List[BacktestEvent] = []
        self._seq = 0
        self._trailing_atr = trailing_atr_multiple

    # -- registration -----------------------------------------------------

    def register(self, setup: Setup, bar_index: int) -> str:
        self._seq += 1
        key = f"{self._symbol}|{self._timeframe}|{setup.setup_type.value}|{setup.timestamp}|{self._seq}"
        self._tracked[key] = TrackedSetup(
            setup=setup, detection_bar=bar_index
        )
        self._events.append(
            BacktestEvent(
                run_key=self._run_key,
                timestamp=setup.timestamp,
                event_type="SETUP_DETECTED",
                symbol=self._symbol,
                timeframe=self._timeframe,
                setup_key=key,
            )
        )
        return key

    def active_qualifying(self) -> List[str]:
        """Keys of setups currently QUALIFIED and not yet entered."""
        out = []
        for key, ts_ in self._tracked.items():
            if ts_.setup.state == SetupState.QUALIFIED and ts_.entry_bar is None:
                out.append(key)
        return out

    def can_enter(self, correlated_symbols: Optional[List[str]] = None) -> bool:
        """Honour the overlap policy and correlation filter.

        ONE_POSITION_PER_SYMBOL: block new entries while a position is open.
        Correlation filter: if correlated_symbols is provided, block new entries
        when any of those symbols already has an open position (BTC/ETH are 80%+
        correlated — double risk = double drawdown).
        """
        if self._config.overlap_policy.upper() == "ONE_POSITION_PER_SYMBOL":
            if len(self._positions) > 0:
                return False
        # Correlation filter: block if correlated asset is already open.
        if correlated_symbols:
            for pos in self._positions.values():
                if pos.setup_key.split("|")[0] in correlated_symbols:
                    return False
        return True

    def enter(self, key: str, raw_open: float, ts: int, bar_index: int,
              normalizer) -> Optional[str]:
        ts_ = self._tracked[key]
        stop, target = self._levels(ts_.setup)
        if stop is None:
            return None                       # no valid stop -> no entry
        side = ts_.setup.side.value
        # A target is usable only if it sits on the profit side of the fill
        # price. Otherwise the position closes by stop/expiry only (honest and
        # conservative — never an inverted target).
        if target is not None:
            if side == "LONG" and target <= raw_open:
                target = None
            if side == "SHORT" and target >= raw_open:
                target = None
        entry_price = ex.effective_entry_price(
            raw_open, side, self._config.slippage_pct, self._config.spread_pct
        )
        # Extract ATR from setup evidence for trailing stop and position sizing.
        atr = ts_.setup.evidence.get("atr") if ts_.setup.evidence else None
        # ATR-based position sizing: risk 1% of notional per trade.
        # stop_distance = |entry - stop|; if ATR available, use 1× ATR as stop.
        # position_risk = entry_price * 0.01 (1% of position value).
        # size = position_risk / stop_distance (normalized to notional).
        stop_distance = abs(entry_price - stop) if stop else None
        if stop_distance and stop_distance > 0:
            risk_pct = 0.01  # 1% risk per trade
            size = (entry_price * risk_pct) / stop_distance
        else:
            size = 1.0  # fallback: unit size
        self._seq += 1
        pos_key = f"P{self._seq}"
        ts_.entry_bar = bar_index
        ts_.setup.state = SetupState.TRIGGERED
        ts_.trade_id = pos_key
        self._positions[pos_key] = OpenPosition(
            pos_key=pos_key,
            setup_key=key,
            side=side,
            entry_price=entry_price,
            entry_raw=raw_open,
            stop=stop,
            target=target,
            entry_ts=ts,
            entry_bar=bar_index,
            high_water=raw_open,
            low_water=raw_open,
            _atr=atr,
        )
        self._events.append(
            BacktestEvent(
                run_key=self._run_key, timestamp=ts, event_type="ENTRY",
                symbol=self._symbol, timeframe=self._timeframe, setup_key=key,
                detail=f"entry={entry_price:.6g} side={side} size={size:.4f}",
            )
        )
        return pos_key

    def _levels(self, setup: Setup):
        """Derive deterministic raw stop/target zones from a setup.

        Stop = structural level (interest area boundary).
        Target = ATR-based 1.5:1 RR minimum (from setup.evidence["atr"]).
        If ATR is unavailable, fall back to zone boundary target.

        LONG stop = interest_area.low, target = stop + 1.5 * ATR.
        SHORT stop = interest_area.high, target = stop - 1.5 * ATR.
        """
        stop = None
        target = None
        if setup.interest_area is not None:
            stop = (
                setup.interest_area.low
                if setup.side.value == "LONG"
                else setup.interest_area.high
            )
            if stop is not None and stop <= 0:
                stop = None
        # ATR-based target with 1.5:1 RR.
        atr = setup.evidence.get("atr") if setup.evidence else None
        if stop is not None and atr is not None and atr > 0:
            rr = 1.5
            if setup.side.value == "LONG":
                target = stop + rr * atr
            else:
                target = stop - rr * atr
        elif setup.targets:
            z = setup.targets[0]
            target = z.high if setup.side.value == "LONG" else z.low
        return stop, target

    # -- per-bar updates ---------------------------------------------------

    def _check_exit(self, pos: OpenPosition, bar: Bar, ts: int, bar_index: int,
                    normalizer) -> Optional[str]:
        """Resolve exit for one open position on the current bar.

        Returns the exit reason if closed, else None. Includes trailing stop
        logic: as the position moves in favor, trail the stop behind the price
        at 1× ATR distance.
        """
        # Trail the stop if position is in profit by at least 1× ATR.
        if self._trailing_atr > 0 and hasattr(pos, '_atr') and pos._atr is not None:
            atr = pos._atr
            if pos.side == "LONG":
                # Only trail once in profit by 1× ATR; trail at 1.5× ATR from high water.
                if pos.high_water >= pos.entry_raw + atr:
                    trailed = pos.high_water - self._trailing_atr * atr
                    if trailed > pos.stop:
                        pos.stop = trailed
            else:
                # Only trail once in profit by 1× ATR; trail at 1.5× ATR from low water.
                if pos.low_water <= pos.entry_raw - atr:
                    trailed = pos.low_water + self._trailing_atr * atr
                    if trailed < pos.stop:
                        pos.stop = trailed

        result = ex.compute_exit(
            bar, pos.side, pos.stop, pos.target, self._config.ambiguous_candle_policy
        )
        # Track excursions regardless of whether we exit this bar.
        if pos.side == "LONG":
            pos.high_water = max(pos.high_water, bar.high)
            pos.low_water = min(pos.low_water, bar.low)
        else:
            pos.high_water = max(pos.high_water, bar.high)
            pos.low_water = min(pos.low_water, bar.low)

        if result is None:
            return None
        exit_price, reason = result
        # Position already closed by effective price; finalize.
        exit_raw = exit_price
        # Expense-adjusted exit runs against the trader.
        fill = ex.exit_price_adjusted(
            exit_price, pos.side, self._config.slippage_pct,
            self._config.spread_pct, reason
        )
        self._finalize(pos, ts, bar_index, fill, exit_raw, reason, normalizer)
        return reason

    def _finalize(self, pos: OpenPosition, ts: int, bar_index: int,
                  exit_price: float, exit_raw: float, reason: str,
                  normalizer) -> None:
        ts_ = self._tracked[pos.setup_key]
        sign = 1.0 if pos.side == "LONG" else -1.0
        gross = (exit_price - pos.entry_price) / pos.entry_price * sign

        entry_fee = self._config.entry_fee_pct
        exit_fee = self._config.exit_fee_pct
        fees = entry_fee + exit_fee
        # Slippage impact approximated as the spread+slippage already applied.
        slippage = self._config.slippage_pct + self._config.spread_pct / 2.0
        slippage_cost = slippage * 2.0
        net = gross - fees - slippage_cost

        holding = ex.duration_bars(pos.entry_ts, ts, self._interval)

        # Excursions in raw price points (signed to side).
        if pos.side == "LONG":
            mfe = pos.high_water - pos.entry_raw
            mae = pos.entry_raw - pos.low_water
        else:
            mfe = pos.entry_raw - pos.low_water
            mae = pos.high_water - pos.entry_raw

        ts_.final_state = SetupState.COMPLETED.value
        trade = BacktestTrade(
            trade_id=pos.pos_key,
            setup_key=pos.setup_key,
            symbol=self._symbol,
            timeframe=self._timeframe,
            setup_type=ts_.setup.setup_type.value,
            regime=ts_.setup.regime.value,
            score=ts_.setup.overall_score,
            score_normalized=normalizer.normalized(ts_.setup.overall_score),
            side=pos.side,
            detection_ts=ts_.setup.timestamp,
            qualification_ts=ts_.qualification_ts or ts_.setup.timestamp,
            entry_ts=pos.entry_ts,
            entry_price=pos.entry_price,
            invalidation=ts_.setup.invalidation or "",
            exit_ts=ts,
            exit_price=exit_price,
            exit_reason=reason,
            gross_return=gross,
            fees=fees,
            slippage=slippage_cost,
            net_return=net,
            holding_bars=holding,
            max_favorable_excursion=mfe,
            max_adverse_excursion=mae,
            overlap=len(self._positions) > 1,
        )
        self._trades.append(trade)
        self._positions.pop(pos.pos_key, None)
        self._events.append(
            BacktestEvent(
                run_key=self._run_key, timestamp=ts, event_type="EXIT",
                symbol=self._symbol, timeframe=self._timeframe,
                setup_key=pos.setup_key, detail=reason,
            )
        )

    # -- lifecycle termination --------------------------------------------

    def close_for_terminal(self, key: str, bar: Bar, ts: int, bar_index: int,
                           normalizer, reason: str) -> None:
        for pos in list(self._positions.values()):
            if pos.setup_key == key:
                fill = ex.exit_price_adjusted(
                    bar.close, pos.side, self._config.slippage_pct,
                    self._config.spread_pct, reason
                )
                self._finalize(pos, ts, bar_index, fill, bar.close, reason, normalizer)

    def position_for(self, key: str) -> Optional[OpenPosition]:
        for pos in self._positions.values():
            if pos.setup_key == key:
                return pos
        return None

    def close_positions_at_end(self, bar: Bar, ts: int, bar_index: int,
                               normalizer) -> None:
        for pos in list(self._positions.values()):
            fill = ex.exit_price_adjusted(
                bar.close, pos.side, self._config.slippage_pct,
                self._config.spread_pct, "END_OF_DATA"
            )
            self._finalize(pos, ts, bar_index, fill, bar.close, "END_OF_DATA", normalizer)

    def close_overaged(self, bar: Bar, ts: int, bar_index: int, normalizer) -> None:
        """Close positions held longer than position_max_bars at bar close."""
        for pos in list(self._positions.values()):
            held = ex.duration_bars(pos.entry_ts, ts, self._interval)
            if held >= self._config.position_max_bars:
                fill = ex.exit_price_adjusted(
                    bar.close, pos.side, self._config.slippage_pct,
                    self._config.spread_pct, "EXPIRED"
                )
                self._finalize(pos, ts, bar_index, fill, bar.close, "EXPIRED", normalizer)

    def record_all(self, final_bar_ts: int, bar_index: int,
                   normalizer) -> List[SetupRecord]:
        """Materialise every tracked setup into a SetupRecord with an outcome."""
        for key, ts_ in self._tracked.items():
            final_state = ts_.final_state or ts_.setup.state.value
            outcome = classify_outcome(final_state, ts_.trade_id)
            self._setups.append(
                SetupRecord(
                    key=key,
                    symbol=self._symbol,
                    timeframe=self._timeframe,
                    detection_ts=ts_.setup.timestamp,
                    setup_type=ts_.setup.setup_type.value,
                    regime=ts_.setup.regime.value,
                    score=ts_.setup.overall_score,
                    score_normalized=normalizer.normalized(ts_.setup.overall_score),
                    side=ts_.setup.side.value,
                    interest_area_low=(
                        ts_.setup.interest_area.low
                        if ts_.setup.interest_area else 0.0
                    ),
                    interest_area_high=(
                        ts_.setup.interest_area.high
                        if ts_.setup.interest_area else 0.0
                    ),
                    invalidation=ts_.setup.invalidation or "",
                    final_state=final_state,
                    outcome=outcome.value,
                    trade_id=ts_.trade_id,
                )
            )
        return self._setups

    # -- accessors --------------------------------------------------------

    @property
    def tracked(self) -> Dict[str, TrackedSetup]:
        return self._tracked

    def live_setup_count(self) -> int:
        """Number of setups still live (non-terminal) right now.

        A setup becomes non-live once it reaches a terminal state
        (INVALIDATED / EXPIRED / COMPLETED). Registrations that have not yet
        turned terminal count as live, so a bar where the strategy is holding a
        usable opportunity is counted as covered.
        """
        n = 0
        for ts_ in self._tracked.values():
            if not _is_terminal(ts_.setup.state):
                n += 1
        return n

    @property
    def positions(self) -> Dict[str, OpenPosition]:
        return self._positions

    @property
    def trades(self) -> List[BacktestTrade]:
        return self._trades

    @property
    def events(self) -> List[BacktestEvent]:
        return self._events