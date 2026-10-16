from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from lotwatcher.cli import add_search, set_search_active
from lotwatcher.db.models import Job, SavedSearch, User
from lotwatcher.db.session import transaction
from lotwatcher.worker import build_source_clients, run_once

EASTERN = ZoneInfo("America/New_York")
BEFORE_6AM = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)  # 05:00 Eastern
AFTER_6AM = datetime(2026, 10, 15, 10, 30, tzinfo=UTC)  # 06:30 Eastern


def jobs(db) -> list[tuple[str, str]]:
    with transaction(db) as session:
        return [(j.key, j.status) for j in session.scalars(select(Job).order_by(Job.id))]


def test_nothing_runs_before_6am(db, make_clients, saved_search):
    assert run_once(db, BEFORE_6AM, EASTERN, make_clients) is None
    assert jobs(db) == []


def test_after_6am_todays_daily_job_is_queued_and_run(db, make_clients, saved_search):
    assert run_once(db, AFTER_6AM, EASTERN, make_clients) == "daily:2026-10-15"

    with transaction(db) as session:
        job = session.scalars(select(Job)).one()
        assert job.status == "done"
        assert job.result["sources"]["marketcheck"]["listings"] == 3


def test_the_same_day_is_not_run_twice(db, make_clients, saved_search):
    run_once(db, AFTER_6AM, EASTERN, make_clients)

    assert run_once(db, AFTER_6AM.replace(hour=15), EASTERN, make_clients) is None
    assert jobs(db) == [("daily:2026-10-15", "done")]


def test_a_crashing_job_is_kept_for_retry_with_its_error(db, saved_search):
    def broken_clients():
        raise RuntimeError("could not build clients")

    run_once(db, AFTER_6AM, EASTERN, broken_clients)

    with transaction(db) as session:
        job = session.scalars(select(Job)).one()
        assert job.status == "queued"
        assert "could not build clients" in job.error


def test_a_missing_key_becomes_a_reason_not_a_crash(db):
    clients = build_source_clients(db, environ={"MARKETCHECK_API_KEY": "fake"})

    assert clients.autodev is None
    assert "AUTODEV_API_KEY" in clients.missing["autodev"]
    assert clients.marketcheck is not None
    assert clients.vpic is not None and clients.nhtsa is not None


# --- command-line helpers ---


def test_add_search_creates_the_user_once_and_the_search(db):
    with transaction(db) as session:
        first = add_search(
            session,
            email="me@example.com",
            home_zip="75201",
            name="RAV4",
            make="Toyota",
            model="RAV4",
            radius_miles=50,
            year_min=2019,
            year_max=2022,
            max_price=30000,
            max_mileage=60000,
        )
        second = add_search(
            session,
            email="me@example.com",
            home_zip="75202",
            name="CR-V",
            make="Honda",
            model="CR-V",
            radius_miles=40,
        )

        assert len(session.scalars(select(User)).all()) == 1
        assert session.scalars(select(User)).one().home_zip == "75202"  # newest wins
        searches = {s.id: s for s in session.scalars(select(SavedSearch))}
        assert searches[first].max_price == 30000
        assert searches[second].make == "Honda"
        assert all(s.active for s in searches.values())


def test_a_search_can_be_paused_and_resumed(db, saved_search):
    with transaction(db) as session:
        set_search_active(session, saved_search, False)
        assert session.get(SavedSearch, saved_search).active is False
        set_search_active(session, saved_search, True)
        assert session.get(SavedSearch, saved_search).active is True
