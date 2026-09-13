"""Real historical data retrieval + provenance manifests for Phase 5.

This module pulls multi-year historical OHLCV from the Binance public API
for the validation datasets (BTC/USDT and ETH/USDT on 4H and 1D), validates
raw candles, writes them to the CandleStore, computes honest data-quality
metrics (gaps, duplicates, ordering, OHLC validity, spacing), and writes a
JSON provenance manifest per (symbol, timeframe).

Every dataset must be reproducible: the manifest records the provider,
retrieval timestamp, exact first/last candle timestamps, counts, and a
content hash so later validation runs are traceable back to a frozen input.
Data validity statuses are explicit: VALID / VALID_WITH_GAPS / INVALID.
We never silently repair or fabricate missing candles.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from ..config import Settings
from ..data.candle_store import CandleStore
from ..data.provider.binance import BinanceProvider
from ..domain.candle import Candle

# Datasets to fetch for Phase 5 validation (multi-year).
DATASETS = [
    ("BTC/USDT", "4H"),
    ("BTC/USDT", "1D"),
    ("ETH/USDT", "4H"),
    ("ETH/USDT", "1D"),
]

# Earliest timestamp to fetch (Binance listing date for these pairs).
# 2017-08-17 00:00:00 UTC in epoch seconds.
DEFAULT_START_TS = 1502928000


def _ts(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _content_hash(candles: List[Candle]) -> str:
    payload = "\n".join(
        f"{c.open_time}|{c.open}|{c.high}|{c.low}|{c.close}|{c.volume}"
        for c in candles
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class DataQualityReport:
    """Honest integrity metrics for a raw fetched series (no repairs)."""

    def __init__(self, candles: List[Candle], interval: int) -> None:
        self.count = len(candles)
        self.interval = interval
        self.chronological = True
        self.duplicate_count = 0
        self.gap_count = 0
        self.invalid_ohlcv = 0
        self.negative_volume = 0
        self.bad_spacing = 0
        self.suspicious_zero = 0
        self.first_ts = candles[0].open_time if candles else None
        self.last_ts = candles[-1].open_time if candles else None
        self._analyze(candles)

    def _analyze(self, candles: List[Candle]) -> None:
        seen: set = set()
        prev_ts: Optional[int] = None
        for c in candles:
            ts = c.open_time
            if ts in seen:
                self.duplicate_count += 1
            seen.add(ts)

            if prev_ts is not None and ts < prev_ts:
                self.chronological = False
            if prev_ts is not None and ts > prev_ts:
                diff = ts - prev_ts
                # A gap is any spacing that is an integer multiple of the
                # interval (exchange suspension/maintenance => missing bars).
                if diff > self.interval:
                    self.gap_count += diff // self.interval - 1
                # Corrupt spacing: a diff that is not an integer multiple of
                # the interval indicates misaligned/corrupt timestamps.
                if diff % self.interval != 0:
                    self.bad_spacing += 1
            prev_ts = ts

            if c.high < c.low or min(c.open, c.close) < c.low or \
                    max(c.open, c.close) > c.high:
                self.invalid_ohlcv += 1
            if c.volume < 0:
                self.negative_volume += 1
            if c.high == 0 or c.low == 0:
                self.suspicious_zero += 1

    @property
    def status(self) -> str:
        if not self.chronological or self.invalid_ohlcv or self.negative_volume or \
                self.bad_spacing:
            return "INVALID"
        if self.gap_count > 0:
            return "VALID_WITH_GAPS"
        return "VALID"

    def as_dict(self) -> dict:
        return {
            "count": self.count,
            "interval_seconds": self.interval,
            "first_ts": self.first_ts,
            "first_iso": _ts(self.first_ts) if self.first_ts else None,
            "last_ts": self.last_ts,
            "last_iso": _ts(self.last_ts) if self.last_ts else None,
            "chronological": self.chronological,
            "duplicate_count": self.duplicate_count,
            "gap_count": self.gap_count,
            "invalid_ohlcv": self.invalid_ohlcv,
            "negative_volume": self.negative_volume,
            "bad_spacing": self.bad_spacing,
            "suspicious_zero": self.suspicious_zero,
            "status": self.status,
        }


class RealDataLoader:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        provider: Optional[BinanceProvider] = None,
        store: Optional[CandleStore] = None,
        manifest_dir: Optional[Path] = None,
        start_ts: int = DEFAULT_START_TS,
        end_ts: Optional[int] = None,
    ) -> None:
        self._settings = settings or Settings()
        self._provider = provider or BinanceProvider()
        self._store = store or CandleStore(self._settings)
        self._manifest_dir = manifest_dir or (
            self._settings.absolute_data_dir / "manifests"
        )
        self._manifest_dir.mkdir(parents=True, exist_ok=True)
        self._start_ts = start_ts
        self._end_ts = end_ts or int(time.time())

    def _interval(self, timeframe: str) -> int:
        return self._settings.timeframe_interval_seconds.get(timeframe, 14400)

    def fetch(self, symbol: str, timeframe: str) -> List[Candle]:
        return self._provider.get_ohlcv(
            symbol, timeframe, start_time=self._start_ts, end_time=self._end_ts
        )

    def _write_manifest(self, record: dict) -> Path:
        symbol = record["symbol"].replace("/", "_")
        path = self._manifest_dir / f"{symbol}_{record['timeframe']}.json"
        path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def ingest_one(self, symbol: str, timeframe: str) -> dict:
        raw = self.fetch(symbol, timeframe)
        interval = self._interval(timeframe)
        quality = DataQualityReport(raw, interval)

        stamped = []
        for c in raw:
            stamped.append(
                Candle(
                    symbol=symbol,
                    timeframe=timeframe,
                    open_time=c.open_time,
                    open=c.open,
                    high=c.high,
                    low=c.low,
                    close=c.close,
                    volume=c.volume,
                )
            )

        stored = self._store.write(symbol, timeframe, stamped)

        manifest = {
            "symbol": symbol,
            "timeframe": timeframe,
            "provider": self._provider.name,
            "provider_version": self._provider.version,
            "retrieval_ts": _ts(int(time.time())),
            "start_ts_requested": self._start_ts,
            "end_ts_requested": self._end_ts,
            "candle_count": quality.count,
            "candles_written": stored,
            "content_hash": _content_hash(raw),
            "quality": quality.as_dict(),
            "data_version": "veyra-phase5-real-v1",
        }
        path = self._write_manifest(manifest)
        manifest["manifest_path"] = str(path)
        return manifest

    def ingest_all(self, datasets: Optional[List[tuple]] = None) -> List[dict]:
        results = []
        for symbol, timeframe in datasets or DATASETS:
            try:
                m = self.ingest_one(symbol, timeframe)
                results.append(m)
                print(
                    f"[real-data] {symbol} {timeframe}: "
                    f"{m['candle_count']} candles, status "
                    f"{m['quality']['status']} -> {m['manifest_path']}"
                )
            except Exception as exc:  # noqa: BLE001 - report per-dataset failure
                print(f"[real-data] {symbol} {timeframe}: FAILED: {exc!r}")
        return results