"""The weekly backfill: MarketCheck's sold or removed cars from the last 90 days, saved as
delisted listings at their last price. They are training data for the Phase 2 price model.

Runs are recorded as "marketcheck_backfill", so they never count toward MarketCheck's daily
delisting (which needs complete daily runs of the active search).
"""

import logging
from datetime import date
from typing import Any

from sqlalchemy import Connection, Engine

from lotwatcher.db.session import transaction
from lotwatcher.ingest.delisting import record_run
from lotwatcher.ingest.normalize import normalize_marketcheck
from lotwatcher.ingest.store import store_sold_listing
from lotwatcher.jobs.daily import SOURCE_ERRORS, active_searches
from lotwatcher.sources.client import SourceClient
from lotwatcher.sources.marketcheck import backfill_recent

log = logging.getLogger(__name__)
RUN_SOURCE = "marketcheck_backfill"


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def run_backfill(
    bind: Engine | Connection, run_date: date, client: SourceClient | None
) -> dict[str, Any]:
    summary: dict[str, Any] = {"run_date": run_date.isoformat(), "saved": 0, "already_tracked": 0}
    summary.update(skipped=0, errors=[])
    for search_id, filters in active_searches(bind):
        if client is None:
            summary["errors"].append("marketcheck is not configured")
            continue
        try:
            result = backfill_recent(client, filters)
        except SOURCE_ERRORS as error:
            with transaction(bind) as session:
                record_run(session, RUN_SOURCE, search_id, run_date, None, error=str(error))
            summary["errors"].append(str(error))
            continue

        with transaction(bind) as session:
            for raw in result.listings:
                last_seen = _date(raw.get("last_seen_at_date"))
                first_seen = _date(raw.get("first_seen_at_date")) or last_seen
                if last_seen is None:
                    summary["skipped"] += 1
                    continue
                try:
                    saved = store_sold_listing(
                        session, normalize_marketcheck(raw), first_seen, last_seen
                    )
                except (KeyError, TypeError, ValueError) as error:
                    summary["skipped"] += 1
                    log.warning("backfill: skipped a malformed listing (%s)", error)
                    continue
                summary["saved" if saved else "already_tracked"] += 1
            record_run(session, RUN_SOURCE, search_id, run_date, result)
    for error in summary["errors"]:
        log.warning("backfill %s: %s", run_date, error)
    return summary
