"""Which jobs should exist by now. The worker queues them; unique keys stop duplicates, so this
can run every minute, and a day missed while the laptop slept runs when it wakes."""

from datetime import datetime, time
from zoneinfo import ZoneInfo

DAILY_RUN_TIME = time(6, 0)
BACKFILL_WEEKDAY = 6  # Sunday (Monday is 0)


def due_jobs(now: datetime, timezone: ZoneInfo) -> list[tuple[str, str, dict]]:
    """(kind, key, payload) for each job due at `now`, using the local date and time."""
    local = now.astimezone(timezone)
    if local.time() < DAILY_RUN_TIME:
        return []
    day = local.date().isoformat()
    jobs = [("daily", f"daily:{day}", {"run_date": day})]
    if local.weekday() == BACKFILL_WEEKDAY:
        year, week, _ = local.isocalendar()
        jobs.append(("backfill", f"backfill:{year}-W{week:02d}", {"run_date": day}))
    return jobs
