"""The shared client every data source uses to make web requests.

Request flow: cache → budget → rate limiter → send → retry temporary failures → cache the result.
API keys are sent with each request but never cached, logged or put in error messages.
"""

import logging
from datetime import timedelta
from typing import Any

import httpx2
from sqlalchemy import Connection, Engine

from lotwatcher.sources.budget import Budget
from lotwatcher.sources.cache import MISS, ResponseCache, cache_key
from lotwatcher.sources.clock import Clock, SystemClock
from lotwatcher.sources.rate_limit import RateLimiter

log = logging.getLogger(__name__)

# httpx2 logs full request URLs, which can contain API keys, so keep its logging quiet
logging.getLogger("httpx2").setLevel(logging.WARNING)

DEFAULT_TTL = timedelta(hours=24)
# Waits before each retry; its length is the number of retries
RETRY_WAITS = (1, 2, 4)


class SourceRequestError(Exception):
    """A request failed for good: a non-temporary error, or temporary errors on every retry."""

    def __init__(self, source: str, path: str, problem: str, status_code: int | None = None):
        super().__init__(f"{source} {path}: {problem}")
        self.source = source
        self.path = path
        self.status_code = status_code


def _is_temporary(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def _retry_after_seconds(response: httpx2.Response) -> float | None:
    """The server's requested wait, when given as a number of seconds."""
    try:
        return float(response.headers["Retry-After"])
    except (KeyError, ValueError):
        return None


class SourceClient:
    def __init__(
        self,
        *,
        source: str,
        base_url: str,
        bind: Engine | Connection,
        calls_per_second: float,
        monthly_cap: int | None,
        secret_params: dict[str, str] | None = None,
        secret_headers: dict[str, str] | None = None,
        transport: httpx2.BaseTransport | None = None,
        clock: Clock | None = None,
        timeout_seconds: float = 30.0,
    ):
        self.source = source
        self._clock = clock or SystemClock()
        self.budget = Budget(bind, source=source, monthly_cap=monthly_cap, clock=self._clock)
        self._cache = ResponseCache(bind, clock=self._clock)
        self._limiter = RateLimiter(calls_per_second, clock=self._clock)
        self._secret_params = dict(secret_params or {})
        self._http = httpx2.Client(
            base_url=base_url,
            headers=secret_headers or {},
            transport=transport,
            timeout=timeout_seconds,
        )

    def get_json(
        self, path: str, params: dict[str, Any] | None = None, ttl: timedelta | None = DEFAULT_TTL
    ) -> Any:
        """GET a JSON response, from the cache when fresh. ttl=None caches it forever."""
        params = dict(params or {})
        key, request = cache_key(self.source, path, params)

        cached = self._cache.get(key)
        if cached is not MISS:
            log.debug("%s %s: answered from cache", self.source, path)
            return cached

        body = self._fetch(path, params)
        self._cache.put(key, self.source, request, body, ttl)
        return body

    def _fetch(self, path: str, params: dict[str, Any]) -> Any:
        for attempt in range(len(RETRY_WAITS) + 1):
            self.budget.reserve_call()
            self._limiter.wait()

            status_code = None
            server_wait = None
            try:
                response = self._http.get(path, params={**params, **self._secret_params})
            except httpx2.TransportError as error:
                # Only the error type: its message can include the full URL with the key
                problem = f"network error ({type(error).__name__})"
            else:
                status_code = response.status_code
                if response.is_success:
                    try:
                        return response.json()
                    except ValueError:
                        raise SourceRequestError(
                            self.source, path, "response was not JSON", status_code
                        ) from None
                problem = f"HTTP {status_code}"
                if not _is_temporary(status_code):
                    raise SourceRequestError(self.source, path, problem, status_code)
                server_wait = _retry_after_seconds(response)

            if attempt == len(RETRY_WAITS):
                raise SourceRequestError(
                    self.source,
                    path,
                    f"gave up after {attempt + 1} attempts, last: {problem}",
                    status_code,
                )
            wait = server_wait if server_wait is not None else RETRY_WAITS[attempt]
            log.warning("%s %s: %s, retrying in %ss", self.source, path, problem, wait)
            self._clock.sleep(wait)

        raise AssertionError("unreachable")
