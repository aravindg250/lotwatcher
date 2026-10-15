import logging

import pytest

from lotwatcher.sources.client import SourceRequestError

from .conftest import NETWORK_ERROR, SECRET_HEADER, SECRET_KEY, ScriptedServer, ok, status


def test_server_error_then_success_retries_once_after_one_second(make_client, clock):
    server = ScriptedServer(status(503), ok({"cars": 3}))

    assert make_client(server).get_json("/listings") == {"cars": 3}
    assert len(server.requests) == 2
    assert clock.sleeps == [1]


def test_too_many_requests_waits_for_retry_after(make_client, clock):
    server = ScriptedServer(status(429, headers={"Retry-After": "7"}), ok())

    make_client(server).get_json("/listings")

    assert clock.sleeps == [7]


def test_network_error_is_retried(make_client):
    server = ScriptedServer(NETWORK_ERROR, ok())

    assert make_client(server).get_json("/listings") == {"ok": True}
    assert len(server.requests) == 2


def test_not_found_is_not_retried(make_client):
    server = ScriptedServer(status(404))

    with pytest.raises(SourceRequestError) as error:
        make_client(server).get_json("/listings")

    assert error.value.status_code == 404
    assert len(server.requests) == 1


def test_gives_up_after_three_retries_with_growing_waits(make_client, clock):
    server = ScriptedServer(status(503), status(503), status(503), status(503))

    with pytest.raises(SourceRequestError):
        make_client(server).get_json("/listings")

    assert len(server.requests) == 4
    assert clock.sleeps == [1, 2, 4]


def test_every_attempt_counts_against_the_budget(make_client):
    server = ScriptedServer(status(503), status(503), ok())
    client = make_client(server)

    client.get_json("/listings")

    assert client.budget.calls_this_month() == 3


def test_api_key_is_sent_but_never_appears_in_errors_or_logs(make_client, caplog):
    server = ScriptedServer(status(503), status(503), status(503), status(503))
    caplog.set_level(logging.DEBUG)

    with pytest.raises(SourceRequestError) as error:
        make_client(server).get_json("/listings")

    sent = server.requests[0]
    assert sent.url.params["api_key"] == SECRET_KEY
    assert sent.headers["Authorization"] == SECRET_HEADER
    for text in (str(error.value), caplog.text):
        assert SECRET_KEY not in text
        assert SECRET_HEADER not in text
