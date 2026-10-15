import logging
import math
from datetime import date

from sqlalchemy import Connection, Engine, text

from lotwatcher.db.session import transaction
from lotwatcher.sources.clock import Clock

log = logging.getLogger(__name__)

# Stop this far below each free-tier cap, in case the provider's count differs from ours
STOP_RESERVE = 0.05


class BudgetExceeded(Exception):
    """A source reached its monthly stop limit. No request was sent."""


class Budget:
    """Counts calls to one source per calendar month (UTC) and refuses calls at the stop limit."""

    def __init__(
        self, bind: Engine | Connection, source: str, monthly_cap: int | None, clock: Clock
    ):
        self._bind = bind
        self._clock = clock
        self.source = source
        self.monthly_cap = monthly_cap
        self.stop_at = None if monthly_cap is None else math.floor(monthly_cap * (1 - STOP_RESERVE))

    def _month(self) -> date:
        return self._clock.now().date().replace(day=1)

    def reserve_call(self) -> None:
        """Count one call before it is sent. Saved at once, so a crash can't lose it."""
        limit = "" if self.stop_at is None else "AND calls < :stop_at"
        params = {"source": self.source, "month": self._month(), "stop_at": self.stop_at}
        with transaction(self._bind) as session:
            session.execute(
                text(
                    "INSERT INTO api_call_counts (source, month) VALUES (:source, :month) "
                    "ON CONFLICT DO NOTHING"
                ),
                params,
            )
            # One statement checks the limit and adds 1, so two calls at once can't both slip past
            counted = session.execute(
                text(
                    "UPDATE api_call_counts SET calls = calls + 1, updated_at = now() "
                    f"WHERE source = :source AND month = :month {limit} RETURNING calls"
                ),
                params,
            ).scalar_one_or_none()

        if counted is None:
            message = (
                f"{self.source} reached its monthly stop limit of {self.stop_at} calls "
                f"(cap {self.monthly_cap}); no more calls to it this month"
            )
            log.warning(message)
            raise BudgetExceeded(message)

    def calls_this_month(self) -> int:
        with transaction(self._bind) as session:
            calls = session.execute(
                text("SELECT calls FROM api_call_counts WHERE source = :source AND month = :month"),
                {"source": self.source, "month": self._month()},
            ).scalar_one_or_none()
        return calls or 0
