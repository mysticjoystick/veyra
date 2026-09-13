"""Setup persistence repository.

Persists setups and their lifecycle events so the full historical path
(DETECTED -> DEVELOPING -> QUALIFIED -> ...) can be reconstructed without
overwriting prior state. Events are append-only; a setup row holds only its
latest known state.
"""

from __future__ import annotations

import json
from typing import List, Optional

from sqlalchemy.orm import Session

from ..domain import Setup as DomainSetup
from ..database.models import Setup as ORMSetup
from ..database.models import SetupEvent


class SetupRepository:
    def __init__(self, session_cls) -> None:
        self._session_cls = session_cls

    def create(self, setup: DomainSetup) -> ORMSetup:
        """Persist a new setup and record its initial (DETECTED) event."""
        with self._session_cls() as session:
            row = self._to_row(setup)
            session.add(row)
            session.flush()
            session.add(
                SetupEvent(
                    setup_id=row.id,
                    event_type=setup.state.value,
                    timestamp=setup.timestamp,
                    detail=json.dumps(setup.to_dict(), default=str),
                )
            )
            session.commit()
            return row

    def update_state(
        self,
        setup_db_id: int,
        setup: DomainSetup,
        new_state: str,
    ) -> ORMSetup:
        """Advance a stored setup's state and append a lifecycle event.

        The event captures the full setup shape at the moment of transition so
        reconstruction of the history is possible.
        """
        with self._session_cls() as session:
            row = session.query(ORMSetup).filter_by(id=setup_db_id).one()
            row.state = new_state
            row.overall_score = setup.overall_score
            row.scores_json = json.dumps(
                {k.value: v for k, v in setup.scores.items()}, default=str
            )
            row.invalidation = setup.invalidation
            row.interest_area_low = (
                setup.interest_area.low if setup.interest_area else None
            )
            row.interest_area_high = (
                setup.interest_area.high if setup.interest_area else None
            )
            row.reasoning = setup.reasoning
            row.expiry_condition = setup.expiry_condition
            session.add(
                SetupEvent(
                    setup_id=row.id,
                    event_type=new_state,
                    timestamp=setup.timestamp,
                    detail=json.dumps(setup.to_dict(), default=str),
                )
            )
            session.commit()
            return row

    def get(self, setup_db_id: int) -> Optional[ORMSetup]:
        with self._session_cls() as session:
            return session.query(ORMSetup).filter_by(id=setup_db_id).one_or_none()

    def find_live(
        self, symbol: str, timeframe: str
    ) -> List[ORMSetup]:
        """Non-terminal setups for a market, most recent first."""
        with self._session_cls() as session:
            return (
                session.query(ORMSetup)
                .filter_by(symbol=symbol, timeframe=timeframe)
                .filter(ORMSetup.state.notin_(["INVALIDATED", "EXPIRED", "COMPLETED"]))
                .order_by(ORMSetup.timestamp.desc())
                .all()
            )

    def events_for(self, setup_db_id: int) -> List[SetupEvent]:
        with self._session_cls() as session:
            return (
                session.query(SetupEvent)
                .filter_by(setup_id=setup_db_id)
                .order_by(SetupEvent.timestamp.asc())
                .all()
            )

    @staticmethod
    def _to_row(setup: DomainSetup) -> ORMSetup:
        return ORMSetup(
            symbol=setup.symbol,
            timeframe=setup.timeframe,
            timestamp=setup.timestamp,
            setup_type=setup.setup_type.value,
            side=setup.side.value,
            regime=setup.regime.value,
            overall_score=setup.overall_score,
            scores_json=json.dumps(
                {k.value: v for k, v in setup.scores.items()}, default=str
            ),
            interest_area_low=setup.interest_area.low if setup.interest_area else None,
            interest_area_high=setup.interest_area.high if setup.interest_area else None,
            invalidation=setup.invalidation,
            targets_json=json.dumps(
                [{"low": z.low, "high": z.high} for z in setup.targets], default=str
            ),
            state=setup.state.value,
            reasoning=setup.reasoning,
            expiry_condition=setup.expiry_condition,
        )