"""MarketCheck: daily active-listing search and weekly backfill of sold or removed cars.
Key goes in the api_key query parameter (see settings.py).

Learned from real recordings and docs (2026-10-15):
- /v2/search/car/active (current stock) and /v2/search/car/recents (sold or expired in the
  last 90 days) share filters and response shape. 50 rows per page at most; paging by start.
- Free plan radius is at most 100 miles. car_type=used works.
- Some listings have no price ("call for price"). No description text.
- dealer.id is a dealer GROUP or shared website (one id covered 7 AutoNation stores).
  The physical lot is mc_dealership.mc_rooftop_id. Dealer latitude/longitude are text.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from lotwatcher.sources.autodev import FIRST_VIN_YEAR
from lotwatcher.sources.client import SourceClient
from lotwatcher.sources.filters import SearchFilters, SearchResult

log = logging.getLogger(__name__)

ACTIVE_PATH = "/v2/search/car/active"
RECENTS_PATH = "/v2/search/car/recents"
ROWS = 50
MAX_RADIUS_MILES = 100  # free-plan maximum
DAILY_PAGE_LIMIT = 5  # agreed budget: about 150 calls a month
BACKFILL_PAGE_LIMIT = 60  # agreed budget: one run a week, about 260 calls a month


def _has_listings_list(body: Any) -> bool:
    return isinstance(body, dict) and isinstance(body.get("listings"), list)


def _base_params(filters: SearchFilters) -> dict[str, str]:
    radius = filters.radius_miles
    if radius > MAX_RADIUS_MILES:
        log.info(
            "marketcheck: radius %s miles limited to the free plan's %s", radius, MAX_RADIUS_MILES
        )
        radius = MAX_RADIUS_MILES
    params = {
        "make": filters.make,
        "model": filters.model,
        "zip": filters.zip,
        "radius": str(radius),
        "car_type": "used",
    }
    if filters.year_min is not None or filters.year_max is not None:
        low = filters.year_min or FIRST_VIN_YEAR
        high = filters.year_max or datetime.now(UTC).year + 1
        params["year_range"] = f"{low}-{high}"
    return params


def _paged_search(
    client: SourceClient, path: str, params: dict[str, str], page_limit: int
) -> SearchResult:
    listings: list[dict[str, Any]] = []
    for page in range(1, page_limit + 1):
        start = (page - 1) * ROWS
        body = client.get_json(
            path,
            params={**params, "rows": str(ROWS), "start": str(start)},
            check=_has_listings_list,
        )
        listings.extend(body["listings"])
        # num_found is the total match count; asking past it is an error (HTTP 422)
        if start + ROWS >= int(body.get("num_found") or 0):
            return SearchResult(listings=listings, pages=page, hit_page_limit=False)
    return SearchResult(listings=listings, pages=page_limit, hit_page_limit=True)


def search_active(
    client: SourceClient, filters: SearchFilters, page_limit: int = DAILY_PAGE_LIMIT
) -> SearchResult:
    """Current used-car listings matching the saved search."""
    params = _base_params(filters)
    if filters.max_price is not None:
        params["price_range"] = f"0-{filters.max_price}"
    if filters.max_mileage is not None:
        params["miles_range"] = f"0-{filters.max_mileage}"
    return _paged_search(client, ACTIVE_PATH, params, page_limit)


def backfill_recent(
    client: SourceClient, filters: SearchFilters, page_limit: int = BACKFILL_PAGE_LIMIT
) -> SearchResult:
    """Sold or removed listings from the last 90 days, newest first: price-model training data.

    No price or mileage limits, so the model sees the whole price and mileage range.
    """
    params = {**_base_params(filters), "sort_by": "last_seen", "sort_order": "desc"}
    return _paged_search(client, RECENTS_PATH, params, page_limit)
