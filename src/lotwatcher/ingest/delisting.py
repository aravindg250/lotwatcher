"""Record each daily search and mark listings delisted after 3 complete runs without them.

A day only counts as a miss for a source when every run of that source that day completed:
no error and no page limit hit. Otherwise a failed call, or pages we never read, would look
like cars being sold. A delisted listing keeps its last price; delisted_on is the first day it
was missing. Together they become price-model training labels.
"""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from lotwatcher.db.models import Listing, SourceRun
from lotwatcher.sources.filters import SearchResult

MISSED_RUNS_TO_DELIST = 3


def record_run(
    session: Session,
    source: str,
    saved_search_id: int | None,
    run_date: date,
    result: SearchResult | None,
    error: str | None = None,
) -> None:
    """Save how one daily search went. A rerun on the same day replaces the record."""
    values = {
        "pages": result.pages if result else 0,
        "listings_seen": len(result.listings) if result else 0,
        "hit_page_limit": result.hit_page_limit if result else False,
        "error": error,
        "completed": result is not None and error is None and not result.hit_page_limit,
    }
    session.execute(
        insert(SourceRun)
        .values(source=source, saved_search_id=saved_search_id, run_date=run_date, **values)
        .on_conflict_do_update(
            constraint="uq_source_runs_source_saved_search_id_run_date",
            set_={**values, "updated_at": func.now()},
        )
    )


def complete_days(session: Session, source: str, up_to: date) -> list[date]:
    """Days (oldest first) on which every run of this source completed."""
    return list(
        session.scalars(
            select(SourceRun.run_date)
            .where(SourceRun.source == source, SourceRun.run_date <= up_to)
            .group_by(SourceRun.run_date)
            .having(func.bool_and(SourceRun.completed))
            .order_by(SourceRun.run_date)
        )
    )


def mark_delisted(session: Session, source: str, today: date) -> int:
    """Delist this source's active listings missing from its last 3 complete runs."""
    days = complete_days(session, source, today)
    if len(days) < MISSED_RUNS_TO_DELIST:
        return 0
    cutoff = days[-MISSED_RUNS_TO_DELIST]

    missing = session.scalars(
        select(Listing).where(
            Listing.source == source,
            Listing.status == "active",
            Listing.last_seen_on < cutoff,
        )
    ).all()
    for listing in missing:
        listing.status = "delisted"
        listing.delisted_on = next(day for day in days if day > listing.last_seen_on)
    session.flush()
    return len(missing)
