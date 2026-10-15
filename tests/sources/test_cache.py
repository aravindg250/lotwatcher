from datetime import timedelta

import pytest
from sqlalchemy import text

from lotwatcher.sources.client import SourceRequestError

from .conftest import SECRET_HEADER, SECRET_KEY, ScriptedServer, ok, status

DAY = timedelta(days=1).total_seconds()


def test_repeat_request_within_a_day_uses_no_call_and_no_budget(make_client):
    server = ScriptedServer(ok({"cars": 3}))
    client = make_client(server)

    first = client.get_json("/listings", params={"make": "Toyota"})
    second = client.get_json("/listings", params={"make": "Toyota"})

    assert first == second == {"cars": 3}
    assert len(server.requests) == 1
    assert client.budget.calls_this_month() == 1


def test_after_a_day_it_calls_again(make_client, clock):
    server = ScriptedServer(ok({"cars": 3}), ok({"cars": 4}))
    client = make_client(server)

    client.get_json("/listings")
    clock.advance(DAY + 1)

    assert client.get_json("/listings") == {"cars": 4}
    assert len(server.requests) == 2


def test_parameter_order_does_not_matter(make_client):
    server = ScriptedServer(ok())
    client = make_client(server)

    client.get_json("/listings", params={"make": "Toyota", "model": "RAV4"})
    client.get_json("/listings", params={"model": "RAV4", "make": "Toyota"})

    assert len(server.requests) == 1


def test_different_parameters_are_different_requests(make_client):
    server = ScriptedServer(ok(), ok())
    client = make_client(server)

    client.get_json("/listings", params={"make": "Toyota"})
    client.get_json("/listings", params={"make": "Honda"})

    assert len(server.requests) == 2


def test_api_key_is_never_stored_in_the_cache(make_client, db):
    make_client(ScriptedServer(ok())).get_json("/listings", params={"make": "Toyota"})

    rows = db.execute(text("SELECT api_cache::text FROM api_cache")).scalars().all()

    assert len(rows) == 1
    assert SECRET_KEY not in rows[0]
    assert SECRET_HEADER not in rows[0]


def test_errors_are_not_cached(make_client):
    server = ScriptedServer(status(404), ok())
    client = make_client(server)

    with pytest.raises(SourceRequestError):
        client.get_json("/listings")

    assert client.get_json("/listings") == {"ok": True}
    assert len(server.requests) == 2


def test_forever_cache_never_expires(make_client, clock):
    server = ScriptedServer(ok({"vin": "decoded"}))
    client = make_client(server)

    client.get_json("/decode", params={"vin": "1HGCM82633A004352"}, ttl=None)
    clock.advance(365 * DAY)

    assert client.get_json("/decode", params={"vin": "1HGCM82633A004352"}, ttl=None) == {
        "vin": "decoded"
    }
    assert len(server.requests) == 1
