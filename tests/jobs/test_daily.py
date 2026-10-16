from datetime import date, timedelta

import httpx2
from sqlalchemy import func, select

from lotwatcher.db.models import Listing, PriceSnapshot, SavedSearch, SourceRun
from lotwatcher.db.session import transaction
from lotwatcher.jobs.backfill import run_backfill
from lotwatcher.jobs.daily import run_daily
from lotwatcher.vin import is_valid_vin

from ..sources.conftest import ok, status
from .conftest import RUN_DATE, RoutedServer, fixture_body


def count(db, model, *where) -> int:
    with transaction(db) as session:
        return session.scalar(select(func.count()).select_from(model).where(*where))


def runs(db) -> dict[str, SourceRun]:
    with transaction(db) as session:
        rows = {r.source: r for r in session.scalars(select(SourceRun))}
        session.expunge_all()  # keep the loaded values readable after the session closes
        return rows


def test_listings_from_both_sources_are_saved_with_snapshots_and_complete_runs(
    db, make_clients, saved_search
):
    summary = run_daily(db, RUN_DATE, make_clients())

    assert count(db, Listing, Listing.source == "autodev") == 2
    assert count(db, Listing, Listing.source == "marketcheck") == 3
    assert count(db, PriceSnapshot) == 5
    assert {s: r.completed for s, r in runs(db).items()} == {"autodev": True, "marketcheck": True}
    assert summary["sources"]["autodev"]["listings"] == 2
    assert summary["sources"]["marketcheck"]["listings"] == 3
    assert summary["sources"]["marketcheck"]["calls_this_month"] == 1


def test_a_rerun_on_the_same_day_adds_nothing(db, make_clients, saved_search):
    run_daily(db, RUN_DATE, make_clients())
    run_daily(db, RUN_DATE, make_clients())

    assert count(db, Listing) == 5
    assert count(db, PriceSnapshot) == 5
    assert count(db, SourceRun) == 2


def test_one_source_failing_does_not_stop_the_other(db, make_clients, servers, saved_search):
    servers["autodev"].routes["/listings"] = lambda: status(503)

    summary = run_daily(db, RUN_DATE, make_clients())

    assert count(db, Listing, Listing.source == "marketcheck") == 3
    autodev_run = runs(db)["autodev"]
    assert autodev_run.completed is False
    assert "HTTP 503" in autodev_run.error
    assert any("autodev" in w for w in summary["warnings"])


def test_a_missing_api_key_is_recorded_and_the_other_source_still_runs(
    db, make_clients, saved_search
):
    summary = run_daily(db, RUN_DATE, make_clients(autodev="AUTODEV_API_KEY is not set"))

    assert runs(db)["autodev"].error == "AUTODEV_API_KEY is not set"
    assert count(db, Listing, Listing.source == "marketcheck") == 3
    assert summary["sources"]["autodev"]["errors"] == ["AUTODEV_API_KEY is not set"]


def test_hitting_the_page_limit_is_a_warning(db, make_clients, servers, saved_search):
    always_more = fixture_body("autodev/search_rav4_page1")  # has a "next" link
    servers["autodev"].routes["/listings"] = lambda: ok(always_more)

    summary = run_daily(db, RUN_DATE, make_clients())

    assert summary["sources"]["autodev"]["hit_page_limit"] is True
    assert any("page limit" in w for w in summary["warnings"])
    assert runs(db)["autodev"].completed is False


def test_a_source_returning_nothing_is_a_warning(db, make_clients, servers, saved_search):
    servers["autodev"].routes["/listings"] = lambda: ok({"data": [], "links": {}})

    summary = run_daily(db, RUN_DATE, make_clients())

    assert any("autodev returned no listings" in w for w in summary["warnings"])


def test_only_valid_vins_are_decoded_and_each_only_once(db, make_clients, servers, saved_search):
    body = fixture_body("autodev/search_rav4_narrow_last_page")
    body["data"][0] = {**body["data"][0], "vin": "1HGCM82633A004353"}  # bad check digit
    servers["autodev"].routes["/listings"] = lambda: ok(body)

    run_daily(db, RUN_DATE, make_clients())
    first_run_decodes = len(servers["vpic"].requests)
    run_daily(db, RUN_DATE + timedelta(days=1), make_clients())

    with transaction(db) as session:
        vins = session.scalars(select(Listing.vin)).all()
    assert first_run_decodes == sum(1 for v in vins if v and is_valid_vin(v)) == 4
    assert len(servers["vpic"].requests) == first_run_decodes  # cached forever


def test_recalls_and_complaints_are_fetched_per_make_model_and_year(
    db, make_clients, servers, saved_search
):
    run_daily(db, RUN_DATE, make_clients())

    with transaction(db) as session:
        rows = session.execute(select(Listing.make, Listing.model, Listing.year)).all()
    # The same NHTSA lookup whatever the capitalisation
    models = {(make.casefold(), model.casefold(), year) for make, model, year in rows}
    paths = [r.url.path for r in servers["nhtsa"].requests]
    assert paths.count("/recalls/recallsByVehicle") == len(models)
    assert paths.count("/complaints/complaintsByVehicle") == len(models)


def test_inactive_saved_searches_are_skipped(db, make_clients, servers, saved_search):
    with transaction(db) as session:
        session.get(SavedSearch, saved_search).active = False

    run_daily(db, RUN_DATE, make_clients())

    assert servers["autodev"].requests == []
    assert count(db, Listing) == 0


# --- weekly backfill ---


def test_backfill_saves_sold_cars_as_delisted_at_their_last_price(db, make_clients, saved_search):
    summary = run_backfill(db, RUN_DATE, make_clients().marketcheck)

    sold = fixture_body("marketcheck/recents_rav4_page1")["listings"]
    with transaction(db) as session:
        rows = {r.source_listing_id: r for r in session.scalars(select(Listing))}
        for raw in sold:
            row = rows[raw["id"]]
            last_seen = date.fromisoformat(raw["last_seen_at_date"][:10])
            assert row.status == "delisted"
            assert row.first_seen_on == date.fromisoformat(raw["first_seen_at_date"][:10])
            assert row.last_seen_on == last_seen
            assert row.delisted_on == last_seen + timedelta(days=1)
            [snap] = session.scalars(
                select(PriceSnapshot).where(PriceSnapshot.listing_id == row.id)
            ).all()
            assert (snap.snapshot_date, snap.price) == (last_seen, raw.get("price"))
    assert summary["saved"] == 3


def test_backfill_leaves_cars_we_already_track_alone(db, make_clients, servers, saved_search):
    sold = fixture_body("marketcheck/recents_rav4_page1")
    active = {**fixture_body("marketcheck/active_rav4_page1"), "num_found": 1}
    active["listings"] = [{**sold["listings"][0], "price": 99999}]
    servers["marketcheck"].routes["/v2/search/car/active"] = lambda: ok(active)
    run_daily(db, RUN_DATE, make_clients())

    run_backfill(db, RUN_DATE, make_clients().marketcheck)

    with transaction(db) as session:
        tracked = session.scalar(
            select(Listing).where(Listing.source_listing_id == sold["listings"][0]["id"])
        )
        assert (tracked.status, tracked.price) == ("active", 99999)


def test_backfill_runs_never_count_toward_marketcheck_delisting(db, make_clients, saved_search):
    run_backfill(db, RUN_DATE, make_clients().marketcheck)

    assert set(runs(db)) == {"marketcheck_backfill"}


def test_unrouted_requests_fail_loudly():
    # Guard for these tests themselves: an unknown path is a 404, never a silent success
    response = RoutedServer({})(httpx2.Request("GET", "https://example.com/unknown"))
    assert response.status_code == 404
