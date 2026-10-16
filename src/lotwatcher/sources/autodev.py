"""Auto.dev listings search. Key goes in an Authorization header (see settings.py).

Learned from real recordings (2026-10-15):
- Free plan: 20 listings per page. The last page has no "next" link.
- Filter names: vehicle.make, vehicle.model, vehicle.year ("2019-2022"),
  retailListing.price and retailListing.miles ("min-max"), zip, distance.
- location is [longitude, latitude] (GeoJSON order).
- Listings have no description and no dealer address, phone or email.
- Every response echoes the account owner's details (email, name, IP, location) under "user".
  They are removed before caching and never stored.
"""

from datetime import UTC, datetime
from typing import Any

from lotwatcher.sources.client import SourceClient
from lotwatcher.sources.filters import SearchFilters, SearchResult

LISTINGS_PATH = "/listings"
PAGE_SIZE = 20  # free-plan maximum; larger requests are silently capped
DAILY_PAGE_LIMIT = 10  # agreed budget: about 300 calls a month for one saved search
FIRST_VIN_YEAR = 1981  # 17-character VINs start with the 1981 model year


def _has_data_list(body: Any) -> bool:
    return isinstance(body, dict) and isinstance(body.get("data"), list)


def _without_account_details(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k != "user"}


def _range(low: int | None, high: int | None) -> str:
    return f"{low}-{high}"


def _params(filters: SearchFilters) -> dict[str, str]:
    params = {
        "vehicle.make": filters.make,
        "vehicle.model": filters.model,
        "zip": filters.zip,
        "distance": str(filters.radius_miles),
    }
    if filters.year_min is not None or filters.year_max is not None:
        params["vehicle.year"] = _range(
            filters.year_min or FIRST_VIN_YEAR,
            filters.year_max or datetime.now(UTC).year + 1,
        )
    if filters.max_price is not None:
        params["retailListing.price"] = _range(0, filters.max_price)
    if filters.max_mileage is not None:
        params["retailListing.miles"] = _range(0, filters.max_mileage)
    return params


def search_listings(
    client: SourceClient, filters: SearchFilters, page_limit: int = DAILY_PAGE_LIMIT
) -> SearchResult:
    """Read pages of listings until there is no next page or the page limit is reached."""
    listings: list[dict[str, Any]] = []
    for page in range(1, page_limit + 1):
        body = client.get_json(
            LISTINGS_PATH,
            params={**_params(filters), "page": str(page), "limit": str(PAGE_SIZE)},
            check=_has_data_list,
            clean=_without_account_details,
        )
        listings.extend(body["data"])
        if "next" not in body.get("links", {}):
            return SearchResult(listings=listings, pages=page, hit_page_limit=False)
    return SearchResult(listings=listings, pages=page_limit, hit_page_limit=True)
