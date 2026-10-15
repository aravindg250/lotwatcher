import pytest

from lotwatcher.sources.rate_limit import RateLimiter


def test_back_to_back_calls_wait_the_minimum_gap(clock):
    limiter = RateLimiter(calls_per_second=5, clock=clock)

    limiter.wait()
    limiter.wait()

    assert clock.sleeps == [pytest.approx(0.2)]


def test_calls_already_spaced_out_do_not_wait(clock):
    limiter = RateLimiter(calls_per_second=5, clock=clock)

    limiter.wait()
    clock.advance(1)
    limiter.wait()

    assert clock.sleeps == []


def test_partly_spaced_calls_wait_only_the_remainder(clock):
    limiter = RateLimiter(calls_per_second=5, clock=clock)

    limiter.wait()
    clock.advance(0.15)
    limiter.wait()

    assert clock.sleeps == [pytest.approx(0.05)]
