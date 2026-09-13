"""Core domain enums and shared types for Veyra.

Free of I/O, UI, and database concerns. These types represent the
canonical domain vocabulary used across all engines.
"""

from __future__ import annotations

from enum import Enum


class Timeframe(str, Enum):
    THREE_MIN = "3m"
    FIVE_MIN = "5m"
    FIFTEEN_MIN = "15m"
    ONE_HOUR = "1H"
    FOUR_HOUR = "4H"
    ONE_DAY = "1D"

    @classmethod
    def supported(cls) -> list[str]:
        return [tf.value for tf in cls]


class Regime(str, Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    UNKNOWN = "UNKNOWN"


class MarketSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class SetupType(str, Enum):
    TREND_CONTINUATION = "TREND_CONTINUATION"
    PULLBACK = "PULLBACK"
    BREAKOUT = "BREAKOUT"
    BREAKOUT_RETEST = "BREAKOUT_RETEST"
    REVERSAL = "REVERSAL"
    RANGE_REJECTION = "RANGE_REJECTION"


class SetupState(str, Enum):
    DETECTED = "DETECTED"
    DEVELOPING = "DEVELOPING"
    QUALIFIED = "QUALIFIED"
    TRIGGERED = "TRIGGERED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    COMPLETED = "COMPLETED"


class SignalType(str, Enum):
    SETUP_QUALIFIED = "SETUP_QUALIFIED"
    SETUP_INVALIDATED = "SETUP_INVALIDATED"
    SETUP_COMPLETED = "SETUP_COMPLETED"
    SETUP_EXPIRED = "SETUP_EXPIRED"


class SystemState(str, Enum):
    WAIT = "WAIT"
    MONITORING = "MONITORING"
    ALERT = "ALERT"


class AnalyticsComponent(str, Enum):
    TREND = "TREND"
    STRUCTURE = "STRUCTURE"
    PULLBACK = "PULLBACK"
    MOMENTUM = "MOMENTUM"
    VOLUME = "VOLUME"
    VOLATILITY = "VOLATILITY"


class TrendDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class StructureState(str, Enum):
    HIGHER_HIGHS_HIGHER_LOWS = "HH_HL"
    LOWER_HIGHS_LOWER_LOWS = "LH_LL"
    HIGHER_HIGHS_LOWER_LOWS = "HH_LL"      # consolidation/diverging
    LOWER_HIGHS_HIGHER_LOWS = "LH_HL"      # tightening
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class StructureAction(str, Enum):
    NONE = "NONE"
    BREAK_OF_STRUCTURE_UP = "BREAK_OF_STRUCTURE_UP"
    BREAK_OF_STRUCTURE_DOWN = "BREAK_OF_STRUCTURE_DOWN"
    FAILED_BREAK = "FAILED_BREAK"


class MomentumState(str, Enum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class VolumeState(str, Enum):
    EXPANDING = "EXPANDING"
    CONTRACTING = "CONTRACTING"
    NORMAL = "NORMAL"
    UNKNOWN = "UNKNOWN"


class VolatilityState(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    EXTREME = "EXTREME"
    UNKNOWN = "UNKNOWN"


class DataQualityState(str, Enum):
    VALID = "VALID"
    VALID_WITH_GAPS = "VALID_WITH_GAPS"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    INVALID = "INVALID"
    UNKNOWN = "UNKNOWN"


class IndicatorState(str, Enum):
    READY = "READY"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    UNKNOWN = "UNKNOWN"


# Re-export setup domain models so they are importable from the package root
# (defined at the end to avoid a circular import with the enums above).
from .setup import PriceZone, Setup  # noqa: E402, F401
