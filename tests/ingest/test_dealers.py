import pytest
from sqlalchemy import func, select

from lotwatcher.db.models import Dealer, DealerSourceId
from lotwatcher.db.session import transaction
from lotwatcher.geo import miles_between
from lotwatcher.ingest.dealers import match_dealer
from lotwatcher.ingest.normalize import NormalizedDealer

# Two points about 0.2 miles apart in Dallas, and one about 10 miles away
HERE = (32.7800, -96.8000)
NEXT_DOOR = (32.7825, -96.8010)
TEN_MILES_AWAY = (32.9250, -96.8000)


def dealer(source="autodev", source_id="a1", name="Sport City Toyota", at=HERE, **extra):
    return NormalizedDealer(
        source=source,
        source_dealer_id=source_id,
        name=name,
        latitude=at[0] if at else None,
        longitude=at[1] if at else None,
        **extra,
    )


def count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def test_distance_helper():
    assert miles_between(*HERE, *HERE) == 0
    assert miles_between(*HERE, *NEXT_DOOR) == pytest.approx(0.18, abs=0.02)
    assert miles_between(*HERE, *TEN_MILES_AWAY) == pytest.approx(10.0, abs=0.2)


def test_first_sighting_creates_a_dealer_and_links_the_source_id(db):
    with transaction(db) as session:
        dealer_id = match_dealer(session, dealer(phone="2145550101"))

        stored = session.get(Dealer, dealer_id)
        assert stored.name == "Sport City Toyota"
        assert stored.phone == "2145550101"
        link = session.scalars(select(DealerSourceId)).one()
        assert (link.source, link.source_dealer_id, link.dealer_id) == ("autodev", "a1", dealer_id)


def test_same_source_id_again_is_the_same_dealer_with_newest_values(db):
    with transaction(db) as session:
        first = match_dealer(session, dealer(street="1 Old Road"))
        again = match_dealer(session, dealer(street="2 New Road"))

        assert again == first
        assert session.get(Dealer, first).street == "2 New Road"
        assert count(session, Dealer) == 1


def test_missing_values_never_erase_known_ones(db):
    with transaction(db) as session:
        first = match_dealer(session, dealer("marketcheck", "r1", phone="2145550101"))
        match_dealer(session, dealer("autodev", "a1", phone=None))

        assert session.get(Dealer, first).phone == "2145550101"


def test_same_dealer_from_two_sources_is_matched_by_name_and_place(db):
    with transaction(db) as session:
        from_marketcheck = match_dealer(session, dealer("marketcheck", "r1", at=HERE))
        from_autodev = match_dealer(session, dealer("autodev", "a1", at=NEXT_DOOR))

        assert from_autodev == from_marketcheck
        assert count(session, Dealer) == 1
        assert count(session, DealerSourceId) == 2


def test_small_name_differences_still_match(db):
    with transaction(db) as session:
        a = match_dealer(session, dealer("marketcheck", "r1", name="Sport City Toyota LLC"))
        b = match_dealer(session, dealer("autodev", "a1", name="Sport City Toyota"))

        assert a == b


def test_same_name_ten_miles_apart_is_two_dealers(db):
    # e.g. two CarMax lots in different towns
    with transaction(db) as session:
        a = match_dealer(session, dealer("marketcheck", "r1", name="Carmax", at=HERE))
        b = match_dealer(session, dealer("autodev", "a1", name="Carmax", at=TEN_MILES_AWAY))

        assert a != b


def test_different_dealers_next_door_are_not_matched(db):
    with transaction(db) as session:
        a = match_dealer(session, dealer("marketcheck", "r1", name="Sport City Toyota"))
        b = match_dealer(session, dealer("autodev", "a1", name="John Eagle Honda", at=NEXT_DOOR))

        assert a != b


def test_a_dealer_without_a_location_is_never_matched_by_name_alone(db):
    with transaction(db) as session:
        a = match_dealer(session, dealer("marketcheck", "r1"))
        b = match_dealer(session, dealer("autodev", "a1", at=None))

        assert a != b
