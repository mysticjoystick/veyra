"""Dataset provenance repository.

Stores and retrieves DatasetRecord provenance rows so a market dataset can
be identified and reproduced (market + timeframe + range + provider + rules).
Also tracks the last updated candle open_time to support incremental updates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from ..database.models import DatasetRecord


@dataclass
class DatasetProvenance:
    symbol: str
    timeframe: str
    provider: str
    start_time: Optional[int]
    end_time: Optional[int]
    candle_count: int
    normalization_version: str = "1"
    provider_version: Optional[str] = None

    @classmethod
    def from_record(cls, record: DatasetRecord) -> "DatasetProvenance":
        return cls(
            symbol=record.symbol,
            timeframe=record.timeframe,
            provider=record.provider,
            start_time=record.start_time,
            end_time=record.end_time,
            candle_count=record.candle_count,
            normalization_version=record.normalization_version,
            provider_version=record.provider_version,
        )


class DatasetRepository:
    def __init__(self, session_cls) -> None:
        self._session_cls = session_cls

    def get(self, symbol: str, timeframe: str) -> Optional[DatasetRecord]:
        with self._session_cls() as session:
            return (
                session.query(DatasetRecord)
                .filter_by(symbol=symbol, timeframe=timeframe)
                .one_or_none()
            )

    def upsert(self, provenance: DatasetProvenance) -> DatasetRecord:
        with self._session_cls() as session:
            record = (
                session.query(DatasetRecord)
                .filter_by(symbol=provenance.symbol, timeframe=provenance.timeframe)
                .one_or_none()
            )
            if record is None:
                record = DatasetRecord(
                    symbol=provenance.symbol, timeframe=provenance.timeframe
                )
                session.add(record)
            record.provider = provenance.provider
            record.start_time = provenance.start_time
            record.end_time = provenance.end_time
            record.candle_count = provenance.candle_count
            record.normalization_version = provenance.normalization_version
            record.provider_version = provenance.provider_version
            session.commit()
            return record

    def latest_open_time(self, symbol: str, timeframe: str) -> Optional[int]:
        """Return end_time from provenance (if known) else from stored data."""
        record = self.get(symbol, timeframe)
        return record.end_time if record else None