"""Database tables for Phase 1.

A vehicle is one real car; a listing is one advertisement for it. Prices are whole dollars.
Listing text (description, raw) comes from outside sources and is untrusted data.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Predictable constraint names, so later migrations can find and change them
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

LISTING_STATUSES = ("active", "delisted")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def id_column() -> Mapped[int]:
    return mapped_column(BigInteger, Identity(), primary_key=True)


def created_at_column() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def updated_at_column() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = id_column()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    home_zip: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = created_at_column()


class SavedSearch(Base):
    __tablename__ = "saved_searches"
    __table_args__ = (
        # Like all check rules, this passes when either year is empty
        CheckConstraint("year_min <= year_max", "year_range"),
        CheckConstraint("max_price >= 0", "max_price_not_negative"),
        CheckConstraint("max_mileage >= 0", "max_mileage_not_negative"),
        CheckConstraint("radius_miles > 0", "radius_positive"),
    )

    id: Mapped[int] = id_column()
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    make: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(100))
    year_min: Mapped[int | None] = mapped_column(Integer)
    year_max: Mapped[int | None] = mapped_column(Integer)
    max_price: Mapped[int | None] = mapped_column(Integer)
    max_mileage: Mapped[int | None] = mapped_column(Integer)
    radius_miles: Mapped[int] = mapped_column(Integer)
    must_haves: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    deal_breakers: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class Dealer(Base):
    __tablename__ = "dealers"

    id: Mapped[int] = id_column()
    name: Mapped[str] = mapped_column(String(300))
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(2))
    zip: Mapped[str | None] = mapped_column(String(10))
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    phone: Mapped[str | None] = mapped_column(String(30))
    email: Mapped[str | None] = mapped_column(String(320))
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class DealerSourceId(Base):
    """Each source's own ID for a dealer, mapped to our merged dealer."""

    __tablename__ = "dealer_source_ids"
    __table_args__ = (UniqueConstraint("source", "source_dealer_id"),)

    id: Mapped[int] = id_column()
    dealer_id: Mapped[int] = mapped_column(ForeignKey("dealers.id"), index=True)
    source: Mapped[str] = mapped_column(String(32))
    source_dealer_id: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = created_at_column()


class Vehicle(Base):
    """One real car. VIN is unique when present; many vehicles may have no VIN."""

    __tablename__ = "vehicles"

    id: Mapped[int] = id_column()
    vin: Mapped[str | None] = mapped_column(String(17), unique=True)
    year: Mapped[int] = mapped_column(Integer)
    make: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(100))
    trim: Mapped[str | None] = mapped_column(String(200))
    engine: Mapped[str | None] = mapped_column(String(200))
    drivetrain: Mapped[str | None] = mapped_column(String(50))
    body_style: Mapped[str | None] = mapped_column(String(100))
    decoded_specs: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class Listing(Base):
    """One advertisement. vehicle_id stays empty until duplicate merging assigns it."""

    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint("source", "source_listing_id"),
        CheckConstraint(f"status IN {LISTING_STATUSES}", "status_valid"),
        CheckConstraint("price >= 0", "price_not_negative"),
        CheckConstraint("mileage >= 0", "mileage_not_negative"),
    )

    id: Mapped[int] = id_column()
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"), index=True)
    source: Mapped[str] = mapped_column(String(32))
    source_listing_id: Mapped[str] = mapped_column(String(200))
    # The VIN this source reported, kept even if the listing is merged or unmerged
    vin: Mapped[str | None] = mapped_column(String(17), index=True)
    # What this source reported; merging needs them before a listing has a vehicle
    year: Mapped[int | None] = mapped_column(Integer)
    make: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(100))
    trim: Mapped[str | None] = mapped_column(String(200))
    dealer_id: Mapped[int | None] = mapped_column(ForeignKey("dealers.id"), index=True)
    url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), server_default=text("'active'"))
    first_seen_on: Mapped[date] = mapped_column(Date)
    last_seen_on: Mapped[date] = mapped_column(Date)
    delisted_on: Mapped[date | None] = mapped_column(Date)
    # Empty for "call for price" listings
    price: Mapped[int | None] = mapped_column(Integer)
    mileage: Mapped[int | None] = mapped_column(Integer)
    color: Mapped[str | None] = mapped_column(String(100))
    photos: Mapped[list[Any]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    description: Mapped[str | None] = mapped_column(Text)
    # The original source data, so normalizing can be re-run without new API calls
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


JOB_STATUSES = ("queued", "running", "done", "failed")


class Job(Base):
    """The Postgres-backed job queue. key is unique (e.g. "daily:2026-10-16"), so the same job
    can never be queued twice."""

    __tablename__ = "jobs"
    __table_args__ = (CheckConstraint(f"status IN {JOB_STATUSES}", "status_valid"),)

    id: Mapped[int] = id_column()
    kind: Mapped[str] = mapped_column(String(32))
    key: Mapped[str] = mapped_column(String(100), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(16), server_default=text("'queued'"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class SourceRun(Base):
    """One daily search of one source. A day only counts as "missing" for delisting when
    every run of that source that day completed (no error, no page limit hit)."""

    __tablename__ = "source_runs"
    __table_args__ = (
        # A rerun replaces the day's row; an empty saved_search_id still counts as one value
        UniqueConstraint(
            "source", "saved_search_id", "run_date", postgresql_nulls_not_distinct=True
        ),
    )

    id: Mapped[int] = id_column()
    source: Mapped[str] = mapped_column(String(32))
    saved_search_id: Mapped[int | None] = mapped_column(ForeignKey("saved_searches.id"))
    run_date: Mapped[date] = mapped_column(Date)
    pages: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    listings_seen: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    hit_page_limit: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    error: Mapped[str | None] = mapped_column(Text)
    completed: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = updated_at_column()


class ApiCallCount(Base):
    """Calls made to one source in one calendar month (UTC). month is the 1st of the month."""

    __tablename__ = "api_call_counts"
    __table_args__ = (CheckConstraint("calls >= 0", "calls_not_negative"),)

    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    updated_at: Mapped[datetime] = updated_at_column()


class ApiCacheEntry(Base):
    """A successful API response, keyed by request. Never holds API keys or secret headers."""

    __tablename__ = "api_cache"

    # SHA-256 of source, path and sorted parameters, without secrets
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(32))
    # Readable copy of the same non-secret request parts, for debugging
    request: Mapped[dict[str, Any]] = mapped_column(JSONB)
    response: Mapped[Any] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Empty means the entry never expires
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PriceSnapshot(Base):
    """One row per listing per day. Price is empty when the listing shows no price."""

    __tablename__ = "price_snapshots"
    __table_args__ = (
        UniqueConstraint("listing_id", "snapshot_date"),
        CheckConstraint("price >= 0", "price_not_negative"),
        CheckConstraint("mileage >= 0", "mileage_not_negative"),
    )

    id: Mapped[int] = id_column()
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.id"))
    snapshot_date: Mapped[date] = mapped_column(Date)
    price: Mapped[int | None] = mapped_column(Integer)
    mileage: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = created_at_column()
