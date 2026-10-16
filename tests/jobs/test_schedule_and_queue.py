from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from lotwatcher.db.models import Job
from lotwatcher.db.session import transaction
from lotwatcher.jobs.queue import MAX_ATTEMPTS, claim, enqueue, fail, finish, requeue_stale
from lotwatcher.jobs.schedule import due_jobs

EASTERN = ZoneInfo("America/New_York")


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


# --- schedule ---


def test_nothing_is_due_before_6am_local():
    # 09:59 UTC is 05:59 Eastern (daylight time)
    assert due_jobs(utc(2026, 10, 15, 9, 59), EASTERN) == []


def test_daily_job_is_due_from_6am_local():
    # 10:00 UTC is 06:00 Eastern
    assert due_jobs(utc(2026, 10, 15, 10, 0), EASTERN) == [
        ("daily", "daily:2026-10-15", {"run_date": "2026-10-15"})
    ]


def test_late_evening_still_uses_the_local_date():
    # 03:00 UTC on the 16th is 23:00 Eastern on the 15th
    assert due_jobs(utc(2026, 10, 16, 3, 0), EASTERN)[0][1] == "daily:2026-10-15"


def test_weekly_backfill_is_due_on_sundays_after_6am():
    # 2026-10-18 is a Sunday
    jobs = due_jobs(utc(2026, 10, 18, 10, 30), EASTERN)

    assert ("backfill", "backfill:2026-W42", {"run_date": "2026-10-18"}) in jobs
    assert all(kind != "backfill" for kind, _, _ in due_jobs(utc(2026, 10, 17, 12), EASTERN))


# --- queue ---


def test_the_same_key_is_only_queued_once(db):
    with transaction(db) as session:
        assert enqueue(session, "daily", "daily:2026-10-15", {}) is True
        assert enqueue(session, "daily", "daily:2026-10-15", {}) is False

        assert len(session.scalars(select(Job)).all()) == 1


def test_claim_takes_the_oldest_due_job_and_marks_it_running(db):
    now = utc(2026, 10, 15, 10)
    with transaction(db) as session:
        enqueue(session, "daily", "daily:2026-10-14", {}, run_after=now - timedelta(days=1))
        enqueue(session, "daily", "daily:2026-10-15", {}, run_after=now)

        job = claim(session, now)

        assert job.key == "daily:2026-10-14"
        assert (job.status, job.attempts, job.started_at) == ("running", 1, now)


def test_claim_ignores_jobs_not_yet_due_and_returns_none_when_empty(db):
    now = utc(2026, 10, 15, 10)
    with transaction(db) as session:
        assert claim(session, now) is None
        enqueue(session, "daily", "daily:later", {}, run_after=now + timedelta(minutes=5))

        assert claim(session, now) is None


def test_finish_stores_the_result(db):
    now = utc(2026, 10, 15, 10)
    with transaction(db) as session:
        enqueue(session, "daily", "k", {}, run_after=now)
        job = claim(session, now)

        finish(session, job, {"listings": 42}, now)

        assert (job.status, job.result, job.finished_at) == ("done", {"listings": 42}, now)


def test_a_failed_job_is_retried_later_until_the_attempt_limit(db):
    now = utc(2026, 10, 15, 10)
    with transaction(db) as session:
        enqueue(session, "daily", "k", {}, run_after=now)
        job = claim(session, now)

        fail(session, job, "boom", now)
        assert (job.status, job.error) == ("queued", "boom")
        assert job.run_after > now

        for _ in range(MAX_ATTEMPTS - 1):
            job.run_after = now
            job = claim(session, now)
            fail(session, job, "boom again", now)

        assert job.status == "failed"
        assert job.attempts == MAX_ATTEMPTS


def test_a_job_stuck_running_after_a_crash_is_queued_again(db):
    now = utc(2026, 10, 15, 10)
    with transaction(db) as session:
        enqueue(session, "daily", "k", {}, run_after=now)
        job = claim(session, now)

        assert requeue_stale(session, now + timedelta(minutes=30)) == 0
        assert requeue_stale(session, now + timedelta(hours=2)) == 1
        assert job.status == "queued"
