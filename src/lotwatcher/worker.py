"""The worker: every minute, queue any jobs that are due (6 a.m. daily, Sunday backfill), then
run queued jobs one at a time. Runs in its own container (docker-compose service "worker").

    python -m lotwatcher.worker
"""

import logging
import os
import time
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, Engine

from lotwatcher.db.models import Job
from lotwatcher.db.session import get_engine, transaction
from lotwatcher.jobs.backfill import run_backfill
from lotwatcher.jobs.daily import SourceClients, run_daily
from lotwatcher.jobs.queue import claim, enqueue, fail, finish, requeue_stale
from lotwatcher.jobs.schedule import due_jobs
from lotwatcher.sources.settings import (
    AUTODEV,
    MARKETCHECK,
    NHTSA,
    VPIC,
    MissingApiKey,
    build_client,
)

log = logging.getLogger("lotwatcher.worker")
CHECK_EVERY_SECONDS = 60

JOBS = {
    "daily": lambda bind, day, clients: run_daily(bind, day, clients),
    "backfill": lambda bind, day, clients: run_backfill(bind, day, clients.marketcheck),
}


def build_source_clients(
    bind: Engine | Connection, environ: Mapping[str, str] = os.environ
) -> SourceClients:
    """Real clients. A missing API key becomes a recorded reason, not a crash."""
    clients: dict = {}
    missing: dict[str, str] = {}
    for settings in (AUTODEV, MARKETCHECK, VPIC, NHTSA):
        try:
            clients[settings.name] = build_client(settings, bind, environ=environ)
        except MissingApiKey as error:
            clients[settings.name] = None
            missing[settings.name] = str(error)
    return SourceClients(**clients, missing=missing)


def run_once(
    bind: Engine | Connection,
    now: datetime,
    timezone: ZoneInfo,
    make_clients: Callable[[], SourceClients],
) -> str | None:
    """Queue due jobs and run at most one. Returns the job key it ran, or None."""
    with transaction(bind) as session:
        requeue_stale(session, now)
        for kind, key, payload in due_jobs(now, timezone):
            # The worker's own clock decides when a job is due, never the database's
            if enqueue(session, kind, key, payload, run_after=now):
                log.info("queued %s", key)

    with transaction(bind) as session:
        job = claim(session, now)
        if job is None:
            return None
        job_id, kind, key = job.id, job.kind, job.key
        run_date = date.fromisoformat(job.payload["run_date"])

    log.info("running %s", key)
    try:
        result = JOBS[kind](bind, run_date, make_clients())
    except Exception as error:  # a crashing job is kept for retry, never lost
        log.exception("%s failed", key)
        with transaction(bind) as session:
            fail(session, session.get(Job, job_id), f"{type(error).__name__}: {error}", now)
        return key

    with transaction(bind) as session:
        finish(session, session.get(Job, job_id), result, now)
    log.info("finished %s: %s", key, result)
    return key


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    timezone = ZoneInfo(os.environ.get("LOTWATCHER_TIMEZONE") or "UTC")
    engine = get_engine()
    log.info("worker started; daily run at 06:00 %s", timezone.key)
    while True:
        ran = run_once(engine, datetime.now(UTC), timezone, lambda: build_source_clients(engine))
        if ran is None:
            time.sleep(CHECK_EVERY_SECONDS)


if __name__ == "__main__":
    main()
