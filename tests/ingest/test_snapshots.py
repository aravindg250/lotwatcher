import json
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import select

from lotwatcher.db.models import Listing, PriceSnapshot, SourceRun
from lotwatcher.db.session import transaction
from lotwatcher.ingest.delisting import mark_delisted, record_run
from lotwatcher.ingest.normalize import normalize_marketcheck
from lotwatcher.ingest.store import store_listing
from lotwatcher.sources.filters import SearchResult

FIXTURES = Path(__file__).parent.parent / "fixtures"
DAY = [date(2026, 10, 15) + timedelta(days=i) for i in range(8)]
COMPLETE = SearchResult(listings=[], pages=1, hit_page_limit=False)
TRUNCATED = SearchResult(listings=[], pages=5, hit_page_limit=True)


def mc_listing(index=0, **changes):
    raw = json.loads((FIXTURES / "marketcheck/active_rav4_page1.json").read_text())
    return normalize_marketcheck({**raw["response"]["listings"][index], **changes})


def snapshots(session, listing_id):
    return session.scalars(
        select(PriceSnapshot)
        .where(PriceSnapshot.listing_id == listing_id)
        .order_by(PriceSnapshot.snapshot_date)
    ).all()


# --- snapshots ---


def test_saving_a_listing_writes_that_days_snapshot(db):
    with transaction(db) as session:
        listing_id = store_listing(session, mc_listing(price=30000, miles=12000), seen_on=DAY[0])

        [snap] = snapshots(session, listing_id)
        assert (snap.snapshot_date, snap.price, snap.mileage) == (DAY[0], 30000, 12000)


def test_a_rerun_on_the_same_day_updates_the_snapshot_instead_of_adding_one(db):
    with transaction(db) as session:
        listing_id = store_listing(session, mc_listing(price=30000), seen_on=DAY[0])
        store_listing(session, mc_listing(price=29000), seen_on=DAY[0])

        [snap] = snapshots(session, listing_id)
        assert snap.price == 29000


def test_every_days_price_is_kept(db):
    with transaction(db) as session:
        listing_id = store_listing(session, mc_listing(price=30000), seen_on=DAY[0])
        store_listing(session, mc_listing(price=29000), seen_on=DAY[1])

        assert [s.price for s in snapshots(session, listing_id)] == [30000, 29000]


def test_call_for_price_snapshot_has_no_price(db):
    with transaction(db) as session:
        listing_id = store_listing(session, mc_listing(price=None), seen_on=DAY[0])

        assert snapshots(session, listing_id)[0].price is None


# --- runs ---


def test_a_rerun_replaces_the_days_run_record(db):
    with transaction(db) as session:
        record_run(session, "marketcheck", None, DAY[0], TRUNCATED)
        record_run(session, "marketcheck", None, DAY[0], COMPLETE)

        [run] = session.scalars(select(SourceRun)).all()
        assert (run.completed, run.hit_page_limit) == (True, False)


def test_a_failed_run_is_recorded_as_incomplete(db):
    with transaction(db) as session:
        record_run(session, "marketcheck", None, DAY[0], None, error="budget reached")

        [run] = session.scalars(select(SourceRun)).all()
        assert run.completed is False
        assert run.error == "budget reached"


# --- delisting ---


def seen_then_missed(session, complete_days_after: list, source="marketcheck") -> Listing:
    """A listing seen on DAY[0], then runs on the given later days that don't include it."""
    listing_id = store_listing(session, mc_listing(price=27000), seen_on=DAY[0])
    record_run(session, source, None, DAY[0], COMPLETE)
    for day, result in complete_days_after:
        record_run(session, source, None, day, result)
    return session.get(Listing, listing_id)


def test_missing_three_complete_runs_in_a_row_is_delisted_at_its_last_price(db):
    with transaction(db) as session:
        listing = seen_then_missed(
            session, [(DAY[1], COMPLETE), (DAY[2], COMPLETE), (DAY[3], COMPLETE)]
        )

        assert mark_delisted(session, "marketcheck", today=DAY[3]) == 1

        assert listing.status == "delisted"
        assert listing.delisted_on == DAY[1]  # the first day it was missing
        assert listing.price == 27000


def test_missing_only_two_runs_stays_active(db):
    with transaction(db) as session:
        listing = seen_then_missed(session, [(DAY[1], COMPLETE), (DAY[2], COMPLETE)])

        assert mark_delisted(session, "marketcheck", today=DAY[2]) == 0
        assert listing.status == "active"


def test_days_with_truncated_or_failed_runs_do_not_count_as_missing(db):
    with transaction(db) as session:
        listing = seen_then_missed(
            session, [(DAY[1], COMPLETE), (DAY[2], TRUNCATED), (DAY[3], None), (DAY[4], COMPLETE)]
        )

        assert mark_delisted(session, "marketcheck", today=DAY[4]) == 0
        assert listing.status == "active"


def test_days_with_no_run_at_all_do_not_count_as_missing(db):
    with transaction(db) as session:
        listing = seen_then_missed(session, [(DAY[1], COMPLETE), (DAY[5], COMPLETE)])

        assert mark_delisted(session, "marketcheck", today=DAY[6]) == 0
        assert listing.status == "active"


def test_another_sources_runs_do_not_count(db):
    with transaction(db) as session:
        listing = seen_then_missed(session, [])
        for day in DAY[1:4]:
            record_run(session, "autodev", None, day, COMPLETE)

        assert mark_delisted(session, "marketcheck", today=DAY[3]) == 0
        assert listing.status == "active"


def test_a_listing_seen_recently_is_not_delisted(db):
    with transaction(db) as session:
        listing = seen_then_missed(session, [(DAY[1], COMPLETE), (DAY[2], COMPLETE)])
        store_listing(session, mc_listing(price=26500), seen_on=DAY[3])
        record_run(session, "marketcheck", None, DAY[3], COMPLETE)

        assert mark_delisted(session, "marketcheck", today=DAY[3]) == 0
        assert listing.status == "active"
