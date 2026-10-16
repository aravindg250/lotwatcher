from dataclasses import dataclass


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
