import json

import httpx2
import pytest
from sqlalchemy import text

from lotwatcher.sources.autodev import search_listings
from lotwatcher.sources.client import UnexpectedResponse
from lotwatcher.sources.filters import SearchFilters
from lotwatcher.sources.settings import AUTODEV, build_client

from .conftest import FIXTURES, ScriptedServer, ok, recorded

FAKE_ENV = {"AUTODEV_API_KEY": "fake-autodev-key"}
PAGE_1 = "autodev/search_rav4_page1"  # 3 listings, has a "next" link
LAST_PAGE = "autodev/search_rav4_narrow_last_page"  # 2 listings, no "next" link

RAV4 = SearchFilters(
    make="Toyota",
    model="RAV4",
    year_min=2019,
    year_max=2022,
    max_price=30000,
    max_mileage=60000,
    zip="75201",
    radius_miles=50,
)


@pytest.fixture
def autodev(db, clock):
    def make(server: ScriptedServer):
        return build_client(
            AUTODEV, db, environ=FAKE_ENV, transport=httpx2.MockTransport(server), clock=clock
        )

    return make


def test_filters_are_sent_with_autodev_parameter_names(autodev):
    server = ScriptedServer(recorded(LAST_PAGE))

    search_listings(autodev(server), RAV4)

    assert dict(server.requests[0].url.params) == {
        "vehicle.make": "Toyota",
        "vehicle.model": "RAV4",
        "vehicle.year": "2019-2022",
        "retailListing.price": "0-30000",
        "retailListing.miles": "0-60000",
        "zip": "75201",
        "distance": "50",
        "page": "1",
        "limit": "20",
    }


def test_filters_left_empty_are_not_sent(autodev):
    server = ScriptedServer(recorded(LAST_PAGE))
    loose = SearchFilters(make="Toyota", model="RAV4", zip="75201", radius_miles=50)

    search_listings(autodev(server), loose)

    params = server.requests[0].url.params
    assert "vehicle.year" not in params
    assert "retailListing.price" not in params
    assert "retailListing.miles" not in params


def test_only_a_minimum_year_is_sent_as_an_open_ended_range(autodev):
    server = ScriptedServer(recorded(LAST_PAGE))
    newer = SearchFilters(make="Toyota", model="RAV4", zip="75201", radius_miles=50, year_min=2020)

    search_listings(autodev(server), newer)

    assert server.requests[0].url.params["vehicle.year"].startswith("2020-")


def test_pages_are_followed_until_there_is_no_next_link(autodev):
    server = ScriptedServer(recorded(PAGE_1), recorded(LAST_PAGE))

    result = search_listings(autodev(server), RAV4)

    assert len(result.listings) == 5
    assert result.pages == 2
    assert result.hit_page_limit is False
    assert [r.url.params["page"] for r in server.requests] == ["1", "2"]


def test_search_stops_at_the_page_limit(autodev):
    server = ScriptedServer(recorded(PAGE_1), recorded(PAGE_1), recorded(PAGE_1))

    result = search_listings(autodev(server), RAV4, page_limit=2)

    assert result.pages == 2
    assert result.hit_page_limit is True
    assert len(server.requests) == 2


def test_an_empty_first_page_gives_no_listings(autodev):
    # Hand-written: a page with no data and no "next" link
    server = ScriptedServer(ok({"data": [], "links": {"self": "https://api.auto.dev/listings"}}))

    result = search_listings(autodev(server), RAV4)

    assert result.listings == []
    assert result.pages == 1


def test_listings_are_returned_as_sent(autodev):
    server = ScriptedServer(recorded(LAST_PAGE))

    listing = search_listings(autodev(server), RAV4).listings[0]

    fixture = json.loads((FIXTURES / f"{LAST_PAGE}.json").read_text())
    assert listing == fixture["response"]["data"][0]


def test_account_details_are_never_stored_in_the_cache(autodev, db):
    body = json.loads((FIXTURES / f"{LAST_PAGE}.json").read_text())["response"]
    body["user"] = {"email": "owner@example.com", "ip": "203.0.113.7"}
    server = ScriptedServer(ok(body))

    search_listings(autodev(server), RAV4)

    stored = db.execute(text("SELECT api_cache::text FROM api_cache")).scalars().all()
    assert len(stored) == 1
    assert "owner@example.com" not in stored[0]
    assert "203.0.113.7" not in stored[0]


def test_a_response_without_a_data_list_raises_and_is_not_cached(autodev):
    server = ScriptedServer(ok({"error": "something changed"}), recorded(LAST_PAGE))
    client = autodev(server)

    with pytest.raises(UnexpectedResponse, match="autodev"):
        search_listings(client, RAV4)

    assert len(search_listings(client, RAV4).listings) == 2
    assert len(server.requests) == 2
