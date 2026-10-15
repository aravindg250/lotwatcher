from datetime import UTC, datetime, timedelta

import httpx2
import pytest

from lotwatcher.sources.client import SourceClient

SECRET_KEY = "SECRET-KEY-123"
SECRET_HEADER = "Bearer SECRET-HEADER-456"


class FakeClock:
    """Pretend time. sleep() returns at once but records the wait and moves time forward."""

    def __init__(self, start: datetime = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)):
        self._now = start
        self._monotonic = 0.0
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        return self._monotonic

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        self._monotonic += seconds
        self._now += timedelta(seconds=seconds)


NETWORK_ERROR = "network error"


class ScriptedServer:
    """A fake API. Returns the scripted responses in order and records every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        if response == NETWORK_ERROR:
            raise httpx2.ConnectError("connection failed", request=request)
        return response


def ok(body=None) -> httpx2.Response:
    return httpx2.Response(200, json=body if body is not None else {"ok": True})


def status(code: int, headers: dict[str, str] | None = None) -> httpx2.Response:
    return httpx2.Response(code, json={"error": code}, headers=headers)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def make_client(db, clock):
    def make(server: ScriptedServer, **overrides) -> SourceClient:
        settings = {
            "source": "testsource",
            "base_url": "https://api.example.test",
            "calls_per_second": 5,
            "monthly_cap": 1000,
            "secret_params": {"api_key": SECRET_KEY},
            "secret_headers": {"Authorization": SECRET_HEADER},
        }
        settings.update(overrides)
        return SourceClient(
            bind=db, transport=httpx2.MockTransport(server), clock=clock, **settings
        )

    return make
