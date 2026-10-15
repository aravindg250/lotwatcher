import logging
from datetime import timedelta

import pytest

from lotwatcher.sources.budget import Budget, BudgetExceeded

from .conftest import ScriptedServer, ok


def test_stop_limit_is_five_percent_below_the_cap(db, clock):
    assert Budget(db, source="autodev", monthly_cap=1000, clock=clock).stop_at == 950
    assert Budget(db, source="marketcheck", monthly_cap=500, clock=clock).stop_at == 475


def test_each_call_adds_one(db, clock):
    budget = Budget(db, source="autodev", monthly_cap=1000, clock=clock)

    budget.reserve_call()
    budget.reserve_call()

    assert budget.calls_this_month() == 2


def test_reaching_the_stop_limit_raises(db, clock):
    budget = Budget(db, source="autodev", monthly_cap=20, clock=clock)  # stops at 19

    for _ in range(19):
        budget.reserve_call()
    with pytest.raises(BudgetExceeded):
        budget.reserve_call()

    assert budget.calls_this_month() == 19


def test_at_the_stop_limit_no_request_is_sent(make_client):
    server = ScriptedServer(ok())
    client = make_client(server, monthly_cap=20)
    for _ in range(19):
        client.budget.reserve_call()

    with pytest.raises(BudgetExceeded):
        client.get_json("/listings")

    assert server.requests == []


def test_reaching_the_stop_limit_logs_a_warning(db, clock, caplog):
    budget = Budget(db, source="autodev", monthly_cap=20, clock=clock)
    for _ in range(19):
        budget.reserve_call()

    with caplog.at_level(logging.WARNING), pytest.raises(BudgetExceeded):
        budget.reserve_call()

    assert "autodev" in caplog.text


def test_sources_are_counted_separately(db, clock):
    autodev = Budget(db, source="autodev", monthly_cap=1000, clock=clock)
    marketcheck = Budget(db, source="marketcheck", monthly_cap=500, clock=clock)

    autodev.reserve_call()
    autodev.reserve_call()
    marketcheck.reserve_call()

    assert autodev.calls_this_month() == 2
    assert marketcheck.calls_this_month() == 1


def test_a_new_month_starts_from_zero(db, clock):
    budget = Budget(db, source="autodev", monthly_cap=1000, clock=clock)  # clock starts Oct 15
    budget.reserve_call()

    clock.advance(timedelta(days=20).total_seconds())  # now Nov 4

    assert budget.calls_this_month() == 0


def test_source_without_a_cap_is_counted_but_never_stopped(db, clock):
    budget = Budget(db, source="nhtsa", monthly_cap=None, clock=clock)

    for _ in range(100):
        budget.reserve_call()

    assert budget.calls_this_month() == 100
