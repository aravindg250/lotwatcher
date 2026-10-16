"""Save normalized listings. Newest values win; seen dates only ever widen.

Price snapshots and delisting are step 6; duplicate merging (vehicle_id) is step 7.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from lotwatcher.db.models import Listing
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
    return row.id
