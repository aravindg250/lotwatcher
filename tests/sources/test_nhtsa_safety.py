from datetime import date, timedelta

import httpx2
import pytest

from lotwatcher.sources.client import UnexpectedResponse
from lotwatcher.sources.nhtsa_safety import (
    ComplaintSummary,
    fetch_complaint_summary,
    fetch_recalls,
)
from lotwatcher.sources.settings import NHTSA, build_client

from .conftest import ScriptedServer, ok, recorded

DAY = timedelta(days=1).total_seconds()


@pytest.fixture
def nhtsa(db, clock):
    def make(server: ScriptedServer):
        return build_client(NHTSA, db, transport=httpx2.MockTransport(server), clock=clock)

    return make


def test_recalls_are_read_from_the_recorded_response(nhtsa):
    server = ScriptedServer(recorded("nhtsa/recalls_rav4_2020"))

    recalls = fetch_recalls(nhtsa(server), "Toyota", "RAV4", 2020)

    assert [r.campaign_number for r in recalls] == ["20V682000", "20V734000", "20V064000"]
    first = recalls[0]
    assert first.component == "FUEL SYSTEM, GASOLINE:DELIVERY:FUEL PUMP"
    assert first.consequence.startswith("If the fuel pump fails")
    assert first.remedy.startswith("Toyota will notify owners")
    assert first.summary.startswith("Toyota Motor Engineering")
    assert (first.park_it, first.park_outside, first.over_the_air) == (False, False, False)


def test_recall_dates_are_day_first(nhtsa):
    # NHTSA sends recall dates as DD/MM/YYYY: "25/11/2020" proves it
    server = ScriptedServer(recorded("nhtsa/recalls_rav4_2020"))

    recalls = fetch_recalls(nhtsa(server), "Toyota", "RAV4", 2020)

    assert [r.report_received for r in recalls] == [
        date(2020, 11, 4),
        date(2020, 11, 25),
        date(2020, 2, 6),
    ]


def test_recalls_request_uses_make_model_and_year(nhtsa):
    server = ScriptedServer(recorded("nhtsa/recalls_rav4_2020"))

    fetch_recalls(nhtsa(server), "Toyota", "RAV4", 2020)

    request = server.requests[0]
    assert request.url.path == "/recalls/recallsByVehicle"
    assert dict(request.url.params) == {"make": "Toyota", "model": "RAV4", "modelYear": "2020"}


def test_no_recalls_gives_an_empty_list(nhtsa):
    # Hand-written: the same shape as the recording, with no results
    server = ScriptedServer(
        ok({"Count": 0, "Message": "Results returned successfully", "results": []})
    )

    assert fetch_recalls(nhtsa(server), "Toyota", "RAV4", 2020) == []


def test_complaints_are_summarized_as_counts(nhtsa):
    server = ScriptedServer(recorded("nhtsa/complaints_rav4_2020"))

    summary = fetch_complaint_summary(nhtsa(server), "Toyota", "RAV4", 2020)

    assert summary == ComplaintSummary(total=3, crashes=1, fires=1, injuries=1, deaths=0)


def test_complaints_request_uses_make_model_and_year(nhtsa):
    server = ScriptedServer(recorded("nhtsa/complaints_rav4_2020"))

    fetch_complaint_summary(nhtsa(server), "Toyota", "RAV4", 2020)

    request = server.requests[0]
    assert request.url.path == "/complaints/complaintsByVehicle"
    assert dict(request.url.params) == {"make": "Toyota", "model": "RAV4", "modelYear": "2020"}


@pytest.mark.parametrize(
    ("fetch", "fixture"),
    [
        (fetch_recalls, "nhtsa/recalls_rav4_2020"),
        (fetch_complaint_summary, "nhtsa/complaints_rav4_2020"),
    ],
)
def test_results_are_cached_for_seven_days(nhtsa, clock, fetch, fixture):
    server = ScriptedServer(recorded(fixture), recorded(fixture))
    client = nhtsa(server)

    fetch(client, "Toyota", "RAV4", 2020)
    clock.advance(6 * DAY)
    fetch(client, "Toyota", "RAV4", 2020)
    assert len(server.requests) == 1

    clock.advance(1 * DAY + 1)
    fetch(client, "Toyota", "RAV4", 2020)
    assert len(server.requests) == 2


@pytest.mark.parametrize("fetch", [fetch_recalls, fetch_complaint_summary])
def test_a_response_without_results_raises_a_clear_error(nhtsa, fetch):
    server = ScriptedServer(ok({"Message": "Something changed"}))

    with pytest.raises(UnexpectedResponse, match="nhtsa"):
        fetch(nhtsa(server), "Toyota", "RAV4", 2020)
