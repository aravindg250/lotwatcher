"""The daily run (Phase 1 part): search every active saved search on each listing source, save
listings and snapshots, record each run, mark delistings, decode new VINs and refresh recalls
and complaints. Duplicate merging (step 7) is added here when built.

One failure never stops the rest: a source, a listing, a VIN decode or a recall lookup that
fails is recorded and reported in the summary. Each source's work is saved on its own, so a
crash keeps what was done, and rerunning a day is safe (everything is keyed by day).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import Connection, Engine, select

from lotwatcher.db.models import Listing, SavedSearch, User
from lotwatcher.db.session import transaction
from lotwatcher.ingest.delisting import mark_delisted, record_run
from lotwatcher.ingest.normalize import NormalizedListing, normalize_autodev, normalize_marketcheck
from lotwatcher.ingest.store import store_listing
from lotwatcher.sources import autodev, marketcheck
from lotwatcher.sources.budget import BudgetExceeded
from lotwatcher.sources.client import SourceClient, SourceRequestError, UnexpectedResponse
from lotwatcher.sources.filters import SearchFilters, SearchResult
from lotwatcher.sources.nhtsa_safety import fetch_complaint_summary, fetch_recalls
from lotwatcher.sources.vpic import decode_vin
from lotwatcher.vin import is_valid_vin

log = logging.getLogger(__name__)

# Errors a source can raise that should be recorded, not crash the run
SOURCE_ERRORS = (BudgetExceeded, SourceRequestError, UnexpectedResponse)
MAX_VIN_DECODES_PER_RUN = 500  # NHTSA is free, but be polite on a big first day

Search = Callable[[SourceClient, SearchFilters], SearchResult]
Normalize = Callable[[dict[str, Any]], NormalizedListing]
LISTING_SOURCES: dict[str, tuple[Search, Normalize]] = {
    "autodev": (autodev.search_listings, normalize_autodev),
    "marketcheck": (marketcheck.search_active, normalize_marketcheck),
}


@dataclass
class SourceClients:
    autodev: SourceClient | None
    marketcheck: SourceClient | None
    vpic: SourceClient | None
    nhtsa: SourceClient | None
    # Why a client is None (e.g. its API key isn't set)
    missing: dict[str, str] = field(default_factory=dict)


def active_searches(bind: Engine | Connection) -> list[tuple[int, SearchFilters]]:
    with transaction(bind) as session:
        rows = session.execute(
            select(SavedSearch, User.home_zip)
            .join(User, SavedSearch.user_id == User.id)
            .where(SavedSearch.active.is_(True))
            .order_by(SavedSearch.id)
        ).all()
        return [
            (
                search.id,
                SearchFilters(
                    make=search.make,
                    model=search.model,
                    zip=home_zip,
                    radius_miles=search.radius_miles,
                    year_min=search.year_min,
                    year_max=search.year_max,
                    max_price=search.max_price,
                    max_mileage=search.max_mileage,
                ),
            )
            for search, home_zip in rows
        ]


def _run_source(
    bind, source: str, client: SourceClient | None, missing_reason: str | None, searches, run_date
) -> dict[str, Any]:
    search, normalize = LISTING_SOURCES[source]
    stats: dict[str, Any] = {
        "listings": 0,
        "skipped": 0,
        "pages": 0,
        "hit_page_limit": False,
        "errors": [],
    }
    for search_id, filters in searches:
        if client is None:
            error = missing_reason or f"{source} is not configured"
            with transaction(bind) as session:
                record_run(session, source, search_id, run_date, None, error=error)
            stats["errors"].append(error)
            continue
        try:
            result = search(client, filters)
        except SOURCE_ERRORS as error:
            with transaction(bind) as session:
                record_run(session, source, search_id, run_date, None, error=str(error))
            stats["errors"].append(str(error))
            continue

        with transaction(bind) as session:
            for raw in result.listings:
                try:
                    store_listing(session, normalize(raw), seen_on=run_date)
                    stats["listings"] += 1
                except (KeyError, TypeError, ValueError) as error:
                    # One malformed listing is skipped, never the whole page
                    stats["skipped"] += 1
                    log.warning("%s: skipped a malformed listing (%s)", source, error)
            record_run(session, source, search_id, run_date, result)
        stats["pages"] += result.pages
        stats["hit_page_limit"] |= result.hit_page_limit

    if client is not None:
        stats["calls_this_month"] = client.budget.calls_this_month()
    return stats


def _listings_seen(bind, run_date: date) -> list[Listing]:
    with transaction(bind) as session:
        rows = session.scalars(select(Listing).where(Listing.last_seen_on == run_date)).all()
        session.expunge_all()
        return list(rows)


def _decode_new_vins(client: SourceClient, listings: list[Listing], warnings: list[str]) -> int:
    vins = sorted({x.vin for x in listings if x.vin and is_valid_vin(x.vin)})
    if len(vins) > MAX_VIN_DECODES_PER_RUN:
        warnings.append(f"vpic: {len(vins)} VINs to decode; doing {MAX_VIN_DECODES_PER_RUN} today")
    decoded = 0
    for vin in vins[:MAX_VIN_DECODES_PER_RUN]:
        try:
            decode_vin(client, vin)  # cached forever, so a VIN is only ever fetched once
            decoded += 1
        except SOURCE_ERRORS as error:
            warnings.append(f"vpic: {error}")
            if isinstance(error, BudgetExceeded):
                break
    return decoded


def _refresh_safety(client: SourceClient, listings: list[Listing], warnings: list[str]) -> int:
    models: dict[tuple[str, str, int], tuple[str, str, int]] = {}
    for x in listings:
        if x.make and x.model and x.year:
            # The same NHTSA lookup whatever the capitalisation
            models.setdefault(
                (x.make.casefold(), x.model.casefold(), x.year), (x.make, x.model, x.year)
            )
    checked = 0
    for make, model, year in models.values():
        try:
            fetch_recalls(client, make, model, year)  # cached 7 days
            fetch_complaint_summary(client, make, model, year)
            checked += 1
        except SOURCE_ERRORS as error:
            warnings.append(f"nhtsa: {error}")
    return checked


def run_daily(bind: Engine | Connection, run_date: date, clients: SourceClients) -> dict[str, Any]:
    """Run one day. Returns a summary (stored on the job and logged)."""
    searches = active_searches(bind)
    summary: dict[str, Any] = {
        "run_date": run_date.isoformat(),
        "saved_searches": len(searches),
        "sources": {},
        "delisted": {},
        "warnings": [],
    }
    warnings = summary["warnings"]

    for source in LISTING_SOURCES:
        client = getattr(clients, source)
        stats = _run_source(bind, source, client, clients.missing.get(source), searches, run_date)
        summary["sources"][source] = stats
        for error in stats["errors"]:
            warnings.append(f"{source}: {error}")
        if searches and not stats["errors"] and stats["listings"] == 0:
            warnings.append(f"{source} returned no listings")
        if stats["hit_page_limit"]:
            warnings.append(
                f"{source} hit its page limit: more listings exist than were read, so it "
                "can't delist anything today (narrow the search or raise the limit)"
            )

    with transaction(bind) as session:
        for source in LISTING_SOURCES:
            summary["delisted"][source] = mark_delisted(session, source, run_date)

    seen = _listings_seen(bind, run_date)
    summary["vins_decoded"] = _decode_new_vins(clients.vpic, seen, warnings) if clients.vpic else 0
    summary["models_checked"] = (
        _refresh_safety(clients.nhtsa, seen, warnings) if clients.nhtsa else 0
    )

    for warning in warnings:
        log.warning("daily %s: %s", run_date, warning)
    return summary
