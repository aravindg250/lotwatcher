import pytest
from alembic import command
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

PHASE1_TABLES = {
    "users",
    "saved_searches",
    "dealers",
    "dealer_source_ids",
    "vehicles",
    "listings",
    "price_snapshots",
}


def add_vehicle(db, vin):
    return db.execute(
        text(
            "INSERT INTO vehicles (vin, year, make, model) "
            "VALUES (:vin, 2020, 'Toyota', 'RAV4') RETURNING id"
        ),
        {"vin": vin},
    ).scalar_one()


def add_listing(
    db, source="autodev", source_listing_id="A1", status="active", price=25000, mileage=30000
):
    return db.execute(
        text(
            "INSERT INTO listings "
            "(source, source_listing_id, status, price, mileage, first_seen_on, last_seen_on) "
            "VALUES (:source, :source_listing_id, :status, :price, :mileage, "
            "CURRENT_DATE, CURRENT_DATE) RETURNING id"
        ),
        {
            "source": source,
            "source_listing_id": source_listing_id,
            "status": status,
            "price": price,
            "mileage": mileage,
        },
    ).scalar_one()


def add_snapshot(db, listing_id, price=25000):
    db.execute(
        text(
            "INSERT INTO price_snapshots (listing_id, snapshot_date, price, mileage) "
            "VALUES (:listing_id, CURRENT_DATE, :price, 30000)"
        ),
        {"listing_id": listing_id, "price": price},
    )


def test_migrations_downgrade_to_empty_and_upgrade_again(alembic_cfg, engine):
    command.downgrade(alembic_cfg, "base")
    assert set(inspect(engine).get_table_names()) == {"alembic_version"}

    command.upgrade(alembic_cfg, "head")
    assert PHASE1_TABLES <= set(inspect(engine).get_table_names())


def test_same_vin_twice_is_rejected(db):
    add_vehicle(db, "1HGCM82633A004352")
    with pytest.raises(IntegrityError):
        add_vehicle(db, "1HGCM82633A004352")


def test_many_vehicles_without_vin_are_allowed(db):
    add_vehicle(db, None)
    add_vehicle(db, None)


def test_same_listing_from_same_source_twice_is_rejected(db):
    add_listing(db, source="autodev", source_listing_id="A1")
    with pytest.raises(IntegrityError):
        add_listing(db, source="autodev", source_listing_id="A1")


def test_same_listing_id_from_different_sources_is_allowed(db):
    add_listing(db, source="autodev", source_listing_id="A1")
    add_listing(db, source="marketcheck", source_listing_id="A1")


def test_two_snapshots_for_same_listing_on_same_day_are_rejected(db):
    listing_id = add_listing(db)
    add_snapshot(db, listing_id)
    with pytest.raises(IntegrityError):
        add_snapshot(db, listing_id, price=24500)


def test_unknown_listing_status_is_rejected(db):
    with pytest.raises(IntegrityError):
        add_listing(db, status="sold")


@pytest.mark.parametrize("field", ["price", "mileage"])
def test_negative_price_or_mileage_is_rejected(db, field):
    with pytest.raises(IntegrityError):
        add_listing(db, **{field: -1})


def test_negative_snapshot_price_is_rejected(db):
    listing_id = add_listing(db)
    with pytest.raises(IntegrityError):
        add_snapshot(db, listing_id, price=-1)
