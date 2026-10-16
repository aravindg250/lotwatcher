"""Turn each source's raw listing into one shared shape. Pure functions: no database, no calls.

Listing text comes from outside sources and is untrusted data: it is stored, never acted on.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from lotwatcher.vin import clean_vin


@dataclass(frozen=True)
class NormalizedDealer:
    source: str
    # Auto.dev dealerId; MarketCheck mc_rooftop_id (one physical lot, not the dealer group)
    source_dealer_id: str
    name: str
    latitude: float | None = None
    longitude: float | None = None
    street: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    phone: str | None = None  # digits only
    email: str | None = None


@dataclass(frozen=True)
class NormalizedListing:
    source: str
    source_listing_id: str
    vin: str | None
    year: int | None
    make: str | None
    model: str | None
    trim: str | None
    price: int | None  # whole dollars; None for "call for price"
    mileage: int | None
    color: str | None
    url: str | None
    description: str | None
    dealer: NormalizedDealer | None
    photos: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def clean_text(value: Any) -> str | None:
    """Trim and collapse whitespace; keep capitals (title-casing would turn RAV4 into Rav4)."""
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def digits_only(value: Any) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits or None


def _whole_number(value: Any) -> int | None:
    try:
        return round(float(value))
    except (TypeError, ValueError):
        return None


def _positive(value: Any) -> int | None:
    """Prices of 0 or less mean "no price shown", never a real $0."""
    number = _whole_number(value)
    return number if number is not None and number > 0 else None


def _not_negative(value: Any) -> int | None:
    number = _whole_number(value)
    return number if number is not None and number >= 0 else None


def _decimal(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_autodev(raw: dict[str, Any]) -> NormalizedListing:
    vehicle = raw.get("vehicle") or {}
    retail = raw.get("retailListing") or {}
    vin = clean_vin(raw.get("vin") or vehicle.get("vin"))

    longitude = latitude = None
    location = raw.get("location")
    if isinstance(location, list) and len(location) == 2:
        longitude, latitude = map(_decimal, location)  # Auto.dev sends [longitude, latitude]

    dealer = None
    if retail.get("dealerId"):
        dealer = NormalizedDealer(
            source="autodev",
            source_dealer_id=str(retail["dealerId"]),
            name=clean_text(retail.get("dealer")) or "(unnamed dealer)",
            latitude=latitude,
            longitude=longitude,
            city=clean_text(retail.get("city")),
            state=clean_text(retail.get("state")),
            zip=clean_text(retail.get("zip")),
        )

    image = clean_text(retail.get("primaryImage"))
    return NormalizedListing(
        source="autodev",
        # Auto.dev has no separate listing id; its own @id link is built from the VIN
        source_listing_id=vin or str(raw.get("@id")),
        vin=vin,
        year=_whole_number(vehicle.get("year")),
        make=clean_text(vehicle.get("make")),
        model=clean_text(vehicle.get("model")),
        trim=clean_text(vehicle.get("trim")),
        price=_positive(retail.get("price")),
        mileage=_not_negative(retail.get("miles")),
        color=clean_text(vehicle.get("exteriorColor")),
        url=clean_text(retail.get("vdp")),
        description=None,  # Auto.dev sends none
        dealer=dealer,
        photos=[image] if image else [],
        raw=raw,
    )


def normalize_marketcheck(raw: dict[str, Any]) -> NormalizedListing:
    build = raw.get("build") or {}
    place = raw.get("mc_dealership") or {}

    dealer = None
    # Only the rooftop id names one physical lot; dealer.id is a group, so never fall back to it
    if place.get("mc_rooftop_id") is not None:
        dealer = NormalizedDealer(
            source="marketcheck",
            source_dealer_id=str(place["mc_rooftop_id"]),
            name=clean_text(place.get("name")) or "(unnamed dealer)",
            latitude=_decimal(place.get("latitude")),
            longitude=_decimal(place.get("longitude")),
            street=clean_text(place.get("street")),
            city=clean_text(place.get("city")),
            state=clean_text(place.get("state")),
            zip=clean_text(place.get("zip")),
            phone=digits_only(place.get("phone")),
            email=clean_text(place.get("seller_email")),
        )

    media = raw.get("media") or {}
    return NormalizedListing(
        source="marketcheck",
        source_listing_id=str(raw["id"]),
        vin=clean_vin(raw.get("vin")),
        year=_whole_number(build.get("year")),
        make=clean_text(build.get("make")),
        model=clean_text(build.get("model")),
        trim=clean_text(build.get("trim")),
        price=_positive(raw.get("price")),
        mileage=_not_negative(raw.get("miles")),
        color=clean_text(raw.get("exterior_color")),
        url=clean_text(raw.get("vdp_url")),
        description=None,  # MarketCheck sends none on this plan
        dealer=dealer,
        photos=[p for p in (media.get("photo_links") or []) if p],
        raw=raw,
    )
