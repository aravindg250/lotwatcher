"""A simple job queue in Postgres (the spec's "Postgres-backed job queue").

claim() uses FOR UPDATE SKIP LOCKED, so two workers can never take the same job.
A failed job is retried later, up to MAX_ATTEMPTS; a job left running by a crash is requeued.
"""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from lotwatcher.db.models import Job

MAX_ATTEMPTS = 3
RETRY_DELAY = timedelta(minutes=10)
STALE_AFTER = timedelta(hours=1)


def enqueue(
    session: Session,
    kind: str,
    key: str,
    payload: dict[str, Any],
    run_after: datetime | None = None,
) -> bool:
    """Queue a job unless one with this key already exists. True if it was queued now."""
    values: dict[str, Any] = {"kind": kind, "key": key, "payload": payload}
    if run_after is not None:
        values["run_after"] = run_after
    created = session.execute(
        insert(Job)
        .values(**values)
        .on_conflict_do_nothing(index_elements=["key"])
        .returning(Job.id)
    ).first()
    return created is not None


def claim(session: Session, now: datetime) -> Job | None:
    """Take the oldest queued job that is due, and mark it running."""
    job = session.scalar(
        select(Job)
        .where(Job.status == "queued", Job.run_after <= now)
        .order_by(Job.run_after, Job.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        return None
    job.status = "running"
    job.attempts += 1
    job.started_at = now
    session.flush()
    return job


def finish(session: Session, job: Job, result: dict[str, Any], now: datetime) -> None:
    job.status = "done"
    job.result = result
    job.error = None
    job.finished_at = now
    session.flush()


def fail(session: Session, job: Job, error: str, now: datetime) -> None:
    """Retry later with a growing delay, or give up after MAX_ATTEMPTS."""
    job.error = error
    if job.attempts >= MAX_ATTEMPTS:
        job.status = "failed"
        job.finished_at = now
    else:
        job.status = "queued"
        job.run_after = now + RETRY_DELAY * job.attempts
    session.flush()


def requeue_stale(session: Session, now: datetime) -> int:
    """Queue again any job still "running" long after it started (the worker crashed)."""
    stale = session.scalars(
        select(Job).where(Job.status == "running", Job.started_at < now - STALE_AFTER)
    ).all()
    for job in stale:
        job.status = "queued"
        job.run_after = now
    session.flush()
    return len(stale)
