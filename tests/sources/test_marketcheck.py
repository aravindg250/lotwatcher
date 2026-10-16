import json
import logging

import httpx2
import pytest

from lotwatcher.sources.client import UnexpectedResponse
from lotwatcher.sources.filters import SearchFilters
from lotwatcher.sources.marketcheck import (
    BACKFILL_PAGE_LIMIT,
    DAILY_PAGE_LIMIT,
    backfill_recent,
    search_active,
)
from lotwatcher.sources.settings import MARKETCHECK, build_client

from .conftest import FIXTURES, ScriptedServer, ok, recorded

FAKE_ENV = {"MARKETCHECK_API_KEY": "fake-marketcheck-key"}
ACTIVE = "marketcheck/active_rav4_page1"  # 3 listings (one with no price)
RECENTS = "marketcheck/recents_rav4_page1"

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


def page(fixture: str, num_found: int) -> httpx2.Response:
    """A recorded page with num_found changed, to steer how many pages there are."""
    body = json.loads((FIXTURES / f"{fixture}.json").read_text())["response"]
    body["num_found"] = num_found
    return ok(body)


@pytest.fixture
def marketcheck(db, clock):
    def make(server: ScriptedServer):
        return build_client(
            MARKETCHECK, db, environ=FAKE_ENV, transport=httpx2.MockTransport(server), clock=clock
        )

    return make


def test_agreed_page_limits():
    assert DAILY_PAGE_LIMIT == 5
    assert BACKFILL_PAGE_LIMIT == 60


def test_active_search_sends_marketcheck_parameter_names(marketcheck):
    server = ScriptedServer(page(ACTIVE, num_found=3))

    search_active(marketcheck(server), RAV4)

    params = dict(server.requests[0].url.params)
    assert params == {
        "make": "Toyota",
        "model": "RAV4",
        "year_range": "2019-2022",
        "price_range": "0-30000",
        "miles_range": "0-60000",
        "zip": "75201",
        "radius": "50",
        "car_type": "used",
        "rows": "50",
        "start": "0",
        "api_key": "fake-marketcheck-key",
    }


def test_radius_over_100_miles_is_limited_to_100_with_a_note(marketcheck, caplog):
    server = ScriptedServer(page(ACTIVE, num_found=3))
    wide = SearchFilters(make="Toyota", model="RAV4", zip="75201", radius_miles=250)

    with caplog.at_level(logging.INFO):
        search_active(marketcheck(server), wide)

    assert server.requests[0].url.params["radius"] == "100"
    assert "100" in caplog.text


def test_pages_continue_until_num_found_is_covered(marketcheck):
    server = ScriptedServer(page(ACTIVE, num_found=60), page(ACTIVE, num_found=60))

    result = search_active(marketcheck(server), RAV4)

    assert [r.url.params["start"] for r in server.requests] == ["0", "50"]
    assert result.pages == 2
    assert result.hit_page_limit is False
    assert len(result.listings) == 6


def test_search_stops_at_the_page_limit(marketcheck):
    server = ScriptedServer(*[page(ACTIVE, num_found=10_000) for _ in range(3)])

    result = search_active(marketcheck(server), RAV4, page_limit=2)

    assert len(server.requests) == 2
    assert result.hit_page_limit is True


def test_no_results_makes_one_call(marketcheck):
    server = ScriptedServer(ok({"num_found": 0, "listings": []}))

    result = search_active(marketcheck(server), RAV4)

    assert result.listings == []
    assert result.pages == 1


def test_listings_are_returned_as_sent_including_call_for_price(marketcheck):
    server = ScriptedServer(recorded(ACTIVE))

    listings = search_active(marketcheck(server), RAV4, page_limit=1).listings

    fixture = json.loads((FIXTURES / f"{ACTIVE}.json").read_text())["response"]["listings"]
    assert listings == fixture
    assert any(listing.get("price") is None for listing in listings)


def test_backfill_uses_recents_without_price_or_mileage_limits(marketcheck):
    server = ScriptedServer(page(RECENTS, num_found=3))

    backfill_recent(marketcheck(server), RAV4)

    request = server.requests[0]
    assert request.url.path == "/v2/search/car/recents"
    params = dict(request.url.params)
    assert "price_range" not in params
    assert "miles_range" not in params
    assert params["sort_by"] == "last_seen"
    assert params["sort_order"] == "desc"
    assert params["year_range"] == "2019-2022"
    assert params["car_type"] == "used"


def test_a_response_without_a_listings_list_raises_and_is_not_cached(marketcheck):
    server = ScriptedServer(ok({"error": "changed"}), page(ACTIVE, num_found=3))
    client = marketcheck(server)

    with pytest.raises(UnexpectedResponse, match="marketcheck"):
        search_active(client, RAV4)

    assert len(search_active(client, RAV4).listings) == 3
    assert len(server.requests) == 2
