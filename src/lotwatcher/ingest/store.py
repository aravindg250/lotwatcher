"""Save normalized listings and their daily price snapshot. Newest values win; seen dates only
ever widen. Delisting is in delisting.py; duplicate merging (vehicle_id) is step 7.
"""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from lotwatcher.db.models import Listing, PriceSnapshot
from lotwatcher.ingest.dealers import match_dealer
from lotwatcher.ingest.normalize import NormalizedListing

FIELDS = (
    "vin",
    "year",
    "make",
    "model",
    "trim",
    "price",
    "mileage",
    "color",
    "url",
    "description",
    "photos",
    "raw",
)


def store_listing(session: Session, listing: NormalizedListing, seen_on: date) -> int:
    """Insert or update one listing as seen on seen_on; returns listings.id."""
    dealer_id = match_dealer(session, listing.dealer) if listing.dealer else None

    row = session.scalar(
        select(Listing).where(
            Listing.source == listing.source,
            Listing.source_listing_id == listing.source_listing_id,
        )
    )
    if row is None:
        row = Listing(
            source=listing.source,
            source_listing_id=listing.source_listing_id,
            first_seen_on=seen_on,
            last_seen_on=seen_on,
        )
        session.add(row)

    for name in FIELDS:
        setattr(row, name, getattr(listing, name))
    if dealer_id is not None:
        row.dealer_id = dealer_id
    # Re-processing an older day must never move these the wrong way
    row.first_seen_on = min(row.first_seen_on, seen_on)
    row.last_seen_on = max(row.last_seen_on, seen_on)
    # Seen today, so it's on sale, even if it was marked delisted before
    row.status = "active"
    row.delisted_on = None
    session.flush()

    # One price snapshot per listing per day; a same-day rerun updates it instead of adding one
    values = {"price": listing.price, "mileage": listing.mileage}
    session.execute(
        insert(PriceSnapshot)
        .values(listing_id=row.id, snapshot_date=seen_on, **values)
        .on_conflict_do_update(
            constraint="uq_price_snapshots_listing_id_snapshot_date", set_=values
        )
    )
    return row.id


def store_sold_listing(
    session: Session, listing: NormalizedListing, first_seen_on: date, last_seen_on: date
) -> int | None:
    """Save a car a source reports as sold or removed (the weekly backfill), as delisted at its
    last price. A listing we already track is left alone: our own observation wins.
    Returns listings.id, or None when it was already tracked."""
    exists = session.scalar(
        select(Listing.id).where(
            Listing.source == listing.source,
            Listing.source_listing_id == listing.source_listing_id,
        )
    )
    if exists is not None:
        return None

    row = Listing(
        source=listing.source,
        source_listing_id=listing.source_listing_id,
        first_seen_on=min(first_seen_on, last_seen_on),
        last_seen_on=last_seen_on,
        status="delisted",
        # The first day it was gone, matching what our own delisting rule records
        delisted_on=last_seen_on + timedelta(days=1),
        dealer_id=match_dealer(session, listing.dealer) if listing.dealer else None,
        **{name: getattr(listing, name) for name in FIELDS},
    )
    session.add(row)
    session.flush()
    session.add(
        PriceSnapshot(
            listing_id=row.id,
            snapshot_date=last_seen_on,
            price=listing.price,
            mileage=listing.mileage,
        )
    )
    session.flush()
    return row.id
