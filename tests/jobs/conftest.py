import json
from collections.abc import Callable
from datetime import date

import httpx2
import pytest

from lotwatcher.db.models import SavedSearch, User
from lotwatcher.db.session import transaction
from lotwatcher.jobs.daily import SourceClients
from lotwatcher.sources.settings import AUTODEV, MARKETCHECK, NHTSA, VPIC, build_client

from ..sources.conftest import FIXTURES, FakeClock, ok

RUN_DATE = date(2026, 10, 15)
FAKE_ENV = {"AUTODEV_API_KEY": "fake-autodev-key", "MARKETCHECK_API_KEY": "fake-mc-key"}


def fixture_body(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())["response"]


def with_num_found(name: str, num_found: int) -> dict:
    return {**fixture_body(name), "num_found": num_found}


class RoutedServer:
    """A fake API that answers by URL path. Each route makes a fresh response every time."""

    def __init__(self, routes: dict[str, Callable[[], httpx2.Response]]):
        self.routes = routes
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        for prefix, respond in self.routes.items():
            if request.url.path.startswith(prefix):
                return respond()
        return httpx2.Response(404, json={"error": "no route"})


def autodev_server(body=None) -> RoutedServer:
    body = body or fixture_body("autodev/search_rav4_narrow_last_page")  # 2 listings, last page
    return RoutedServer({"/listings": lambda: ok(body)})


def marketcheck_server() -> RoutedServer:
    return RoutedServer(
        {
            "/v2/search/car/active": lambda: ok(with_num_found("marketcheck/active_rav4_page1", 3)),
            "/v2/search/car/recents": lambda: ok(
                with_num_found("marketcheck/recents_rav4_page1", 3)
            ),
        }
    )


def vpic_server() -> RoutedServer:
    body = fixture_body("vpic/decode_1HGCM82633A004352")
    return RoutedServer({"/api/vehicles/DecodeVinValues": lambda: ok(body)})


def nhtsa_server() -> RoutedServer:
    recalls = fixture_body("nhtsa/recalls_rav4_2020")
    complaints = fixture_body("nhtsa/complaints_rav4_2020")
    return RoutedServer({"/recalls": lambda: ok(recalls), "/complaints": lambda: ok(complaints)})


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def servers():
    return {
        "autodev": autodev_server(),
        "marketcheck": marketcheck_server(),
        "vpic": vpic_server(),
        "nhtsa": nhtsa_server(),
    }


@pytest.fixture
def make_clients(db, clock, servers):
    def make(**missing: str) -> SourceClients:
        def client(settings):
            if settings.name in missing:
                return None
            transport = httpx2.MockTransport(servers[settings.name])
            return build_client(settings, db, environ=FAKE_ENV, transport=transport, clock=clock)

        return SourceClients(
            autodev=client(AUTODEV),
            marketcheck=client(MARKETCHECK),
            vpic=client(VPIC),
            nhtsa=client(NHTSA),
            missing=missing,
        )

    return make


@pytest.fixture
def saved_search(db):
    with transaction(db) as session:
        user = User(email="me@example.com", home_zip="75201")
        session.add(user)
        session.flush()
        search = SavedSearch(
            user_id=user.id,
            name="RAV4",
            make="Toyota",
            model="RAV4",
            year_min=2019,
            year_max=2022,
            radius_miles=50,
        )
        session.add(search)
        session.flush()
        return search.id
