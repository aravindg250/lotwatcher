import json
from datetime import date
from pathlib import Path

from sqlalchemy import select

from lotwatcher.db.models import Listing
from lotwatcher.db.session import transaction
from lotwatcher.ingest.normalize import normalize_autodev, normalize_marketcheck
from lotwatcher.ingest.store import store_listing

FIXTURES = Path(__file__).parent.parent / "fixtures"
DAY_1 = date(2026, 10, 15)
DAY_2 = date(2026, 10, 16)


def raw(name: str, key: str, index: int = 0) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())["response"][key][index]


def mc_listing(**changes):
    return normalize_marketcheck({**raw("marketcheck/active_rav4_page1", "listings"), **changes})


def test_new_listing_is_saved_with_its_dealer_and_seen_dates(db):
    listing = mc_listing()
    with transaction(db) as session:
        listing_id = store_listing(session, listing, seen_on=DAY_1)

        row = session.get(Listing, listing_id)
        assert (row.source, row.source_listing_id, row.vin) == (
            "marketcheck",
            listing.source_listing_id,
            listing.vin,
        )
        assert (row.year, row.make, row.model, row.trim) == (
            listing.year,
            listing.make,
            listing.model,
            listing.trim,
        )
        assert (row.price, row.mileage, row.status) == (listing.price, listing.mileage, "active")
        assert (row.first_seen_on, row.last_seen_on) == (DAY_1, DAY_1)
        assert row.dealer_id is not None
        assert row.vehicle_id is None  # set by duplicate merging (step 7)
        assert row.raw == listing.raw


def test_seen_again_updates_newest_values_and_last_seen_only(db):
    with transaction(db) as session:
        first = store_listing(session, mc_listing(price=30000), seen_on=DAY_1)
        again = store_listing(session, mc_listing(price=28500), seen_on=DAY_2)

        assert again == first
        row = session.get(Listing, first)
        assert row.price == 28500
        assert (row.first_seen_on, row.last_seen_on) == (DAY_1, DAY_2)
        assert len(session.scalars(select(Listing)).all()) == 1


def test_a_delisted_listing_that_reappears_is_active_again(db):
    with transaction(db) as session:
        listing_id = store_listing(session, mc_listing(), seen_on=DAY_1)
        row = session.get(Listing, listing_id)
        row.status, row.delisted_on = "delisted", DAY_1

        store_listing(session, mc_listing(), seen_on=DAY_2)

        assert (row.status, row.delisted_on) == ("active", None)


def test_same_car_from_two_sources_is_two_listings_until_merging(db):
    autodev = normalize_autodev(raw("autodev/search_rav4_page1", "data"))
    marketcheck = mc_listing(vin=autodev.vin)
    with transaction(db) as session:
        a = store_listing(session, autodev, seen_on=DAY_1)
        b = store_listing(session, marketcheck, seen_on=DAY_1)

        assert a != b
        assert session.get(Listing, a).vin == session.get(Listing, b).vin
