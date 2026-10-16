"""Match each source's dealer to one dealers row.

1. A source dealer id seen before goes to the same dealer (newest known values win).
2. Otherwise, a dealer with a similar name (85%+) within 1 mile is the same dealer.
   Distance keeps two lots of one chain apart; phone alone is not used, because dealer groups
   share call-centre numbers. A dealer with no location is never matched by name alone.
3. Otherwise it's a new dealer.
"""

import re
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from lotwatcher.db.models import Dealer, DealerSourceId
from lotwatcher.geo import miles_between
from lotwatcher.ingest.normalize import NormalizedDealer

NAME_SIMILARITY = 0.85
MAX_MILES = 1.0
# About 1.4 miles of latitude: a cheap first filter before the exact distance check
SEARCH_BOX_DEGREES = 0.02
FIELDS = ("name", "street", "city", "state", "zip", "latitude", "longitude", "phone", "email")


def _name_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def name_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _name_key(a), _name_key(b)).ratio()


def _update(row: Dealer, dealer: NormalizedDealer) -> None:
    for name in FIELDS:
        value = getattr(dealer, name)
        if value is not None:  # a source that lacks a value never erases a known one
            setattr(row, name, value)


def _find_nearby_same_name(session: Session, dealer: NormalizedDealer) -> Dealer | None:
    if dealer.latitude is None or dealer.longitude is None:
        return None
    candidates = session.scalars(
        select(Dealer).where(
            Dealer.latitude.between(
                dealer.latitude - SEARCH_BOX_DEGREES, dealer.latitude + SEARCH_BOX_DEGREES
            ),
            Dealer.longitude.between(
                dealer.longitude - SEARCH_BOX_DEGREES, dealer.longitude + SEARCH_BOX_DEGREES
            ),
        )
    )
    matches = []
    for row in candidates:
        similarity = name_similarity(row.name, dealer.name)
        miles = miles_between(row.latitude, row.longitude, dealer.latitude, dealer.longitude)
        if similarity >= NAME_SIMILARITY and miles <= MAX_MILES:
            matches.append((-similarity, miles, row.id, row))
    return min(matches)[3] if matches else None


def match_dealer(session: Session, dealer: NormalizedDealer) -> int:
    """The dealers.id for this source dealer, creating or linking rows as needed."""
    link = session.scalar(
        select(DealerSourceId).where(
            DealerSourceId.source == dealer.source,
            DealerSourceId.source_dealer_id == dealer.source_dealer_id,
        )
    )
    if link is not None:
        row = session.get(Dealer, link.dealer_id)
        _update(row, dealer)
        return row.id

    row = _find_nearby_same_name(session, dealer)
    if row is None:
        row = Dealer(name=dealer.name)
        session.add(row)
    _update(row, dealer)
    session.flush()
    session.add(
        DealerSourceId(
            dealer_id=row.id, source=dealer.source, source_dealer_id=dealer.source_dealer_id
        )
    )
    session.flush()
    return row.id
