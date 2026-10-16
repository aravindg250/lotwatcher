from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SearchFilters:
    """What to search for, built from a saved search. Shared by all listing sources."""

    make: str
    model: str
    zip: str
    radius_miles: int
    year_min: int | None = None
    year_max: int | None = None
    max_price: int | None = None
    max_mileage: int | None = None


@dataclass(frozen=True)
class SearchResult:
    """Raw listings from one source's search, as sent."""

    listings: list[dict[str, Any]]
    pages: int
    # True when more pages existed but the page limit stopped the search
    hit_page_limit: bool
