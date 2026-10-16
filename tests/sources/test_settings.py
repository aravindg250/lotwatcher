import httpx2
import pytest

from lotwatcher.sources.settings import (
    AUTODEV,
    MARKETCHECK,
    NHTSA,
    VPIC,
    MissingApiKey,
    build_client,
)

from .conftest import ScriptedServer, ok

FAKE_ENV = {"AUTODEV_API_KEY": "fake-autodev-key", "MARKETCHECK_API_KEY": "fake-marketcheck-key"}


def send_one_request(settings, db, clock, environ=FAKE_ENV) -> httpx2.Request:
    server = ScriptedServer(ok())
    client = build_client(
        settings, db, environ=environ, transport=httpx2.MockTransport(server), clock=clock
    )
    client.get_json("/anything", params={"make": "Toyota"})
    return server.requests[0]


def test_settings_match_the_agreed_limits():
    assert (AUTODEV.calls_per_second, AUTODEV.monthly_cap) == (5, 1000)
    assert (MARKETCHECK.calls_per_second, MARKETCHECK.monthly_cap) == (5, 500)
    assert (NHTSA.calls_per_second, NHTSA.monthly_cap) == (1, None)
    assert (VPIC.calls_per_second, VPIC.monthly_cap) == (1, None)


def test_autodev_key_is_sent_as_a_bearer_header_only(db, clock):
    request = send_one_request(AUTODEV, db, clock)

    assert request.headers["Authorization"] == "Bearer fake-autodev-key"
    assert "fake-autodev-key" not in str(request.url)


def test_marketcheck_key_is_sent_as_a_query_parameter_only(db, clock):
    request = send_one_request(MARKETCHECK, db, clock)

    assert request.url.params["api_key"] == "fake-marketcheck-key"
    assert "Authorization" not in request.headers


@pytest.mark.parametrize("settings", [NHTSA, VPIC])
def test_nhtsa_sources_send_no_key(settings, db, clock):
    request = send_one_request(settings, db, clock)

    assert "Authorization" not in request.headers
    assert "fake-" not in str(request.url)


def test_missing_key_raises_a_clear_error_naming_the_variable(db, clock):
    with pytest.raises(MissingApiKey, match="AUTODEV_API_KEY"):
        send_one_request(AUTODEV, db, clock, environ={})
