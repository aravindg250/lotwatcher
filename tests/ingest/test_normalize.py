import json
from pathlib import Path

import pytest

from lotwatcher.ingest.normalize import (
    clean_text,
    digits_only,
    normalize_autodev,
    normalize_marketcheck,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


def listings(name: str, key: str) -> list[dict]:
    return json.loads((FIXTURES / f"{name}.json").read_text())["response"][key]


@pytest.fixture
def autodev_raw():
    return listings("autodev/search_rav4_page1", "data")[0]


@pytest.fixture
def mc_active():
    return listings("marketcheck/active_rav4_page1", "listings")


def test_autodev_listing_fields(autodev_raw):
    n = normalize_autodev(autodev_raw)

    assert n.source == "autodev"
    assert n.source_listing_id == autodev_raw["vin"]  # Auto.dev has no separate listing id
    assert n.vin == autodev_raw["vin"]
    assert (n.year, n.make, n.model) == (autodev_raw["vehicle"]["year"], "Toyota", "RAV4")
    assert n.trim == autodev_raw["vehicle"]["trim"]
    assert n.price == autodev_raw["retailListing"]["price"]
    assert n.mileage == autodev_raw["retailListing"]["miles"]
    assert n.color == autodev_raw["vehicle"]["exteriorColor"]
    assert n.photos == [autodev_raw["retailListing"]["primaryImage"]]
    assert n.url == autodev_raw["retailListing"]["vdp"]
    assert n.description is None
    assert n.raw == autodev_raw


def test_autodev_dealer_and_location_order(autodev_raw):
    dealer = normalize_autodev(autodev_raw).dealer
    longitude, latitude = autodev_raw["location"]  # Auto.dev sends [longitude, latitude]

    assert dealer.source == "autodev"
    assert dealer.source_dealer_id == autodev_raw["retailListing"]["dealerId"]
    assert dealer.name == autodev_raw["retailListing"]["dealer"]
    assert (dealer.latitude, dealer.longitude) == (latitude, longitude)
    assert dealer.state == "TX"
    assert dealer.phone is None  # Auto.dev gives no dealer phone


def test_marketcheck_listing_fields(mc_active):
    raw = mc_active[0]
    n = normalize_marketcheck(raw)

    assert n.source == "marketcheck"
    assert n.source_listing_id == raw["id"]
    assert n.vin == raw["vin"]
    assert (n.year, n.make, n.model, n.trim) == (
        raw["build"]["year"],
        raw["build"]["make"],
        raw["build"]["model"],
        raw["build"]["trim"],
    )
    assert n.price == raw["price"]
    assert n.mileage == raw["miles"]
    assert n.color == raw["exterior_color"]
    assert n.photos == raw["media"]["photo_links"]
    assert n.url == raw["vdp_url"]


def test_marketcheck_dealer_is_the_rooftop_not_the_group(mc_active):
    raw = mc_active[0]
    dealer = normalize_marketcheck(raw).dealer
    place = raw["mc_dealership"]

    assert dealer.source_dealer_id == str(place["mc_rooftop_id"])
    assert dealer.source_dealer_id != str(raw["dealer"]["id"])
    assert dealer.name == place["name"]
    assert dealer.street == place["street"]
    assert dealer.latitude == float(place["latitude"])  # MarketCheck sends text
    assert dealer.phone == digits_only(place["phone"])


def test_call_for_price_listing_has_no_price(mc_active):
    no_price = next(x for x in mc_active if x.get("price") is None)

    assert normalize_marketcheck(no_price).price is None


@pytest.mark.parametrize("price", [0, -1])
def test_zero_or_negative_price_means_no_price(mc_active, price):
    raw = {**mc_active[0], "price": price}

    assert normalize_marketcheck(raw).price is None


def test_vin_is_cleaned(mc_active):
    raw = {**mc_active[0], "vin": "  " + mc_active[0]["vin"].lower() + " "}

    assert normalize_marketcheck(raw).vin == mc_active[0]["vin"]


def test_missing_values_become_none(mc_active):
    raw = {**mc_active[0], "miles": None, "exterior_color": "", "media": {}}

    n = normalize_marketcheck(raw)

    assert n.mileage is None
    assert n.color is None
    assert n.photos == []


def test_clean_text_trims_and_collapses_spaces_but_keeps_capitals():
    assert clean_text("  XLE   Premium\tAWD ") == "XLE Premium AWD"
    assert clean_text("RAV4") == "RAV4"
    assert clean_text("   ") is None
    assert clean_text(None) is None


def test_digits_only():
    assert digits_only("(214) 555-0101") == "2145550101"
    assert digits_only("") is None
    assert digits_only(None) is None
