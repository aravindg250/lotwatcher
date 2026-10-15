import hashlib
import json
from datetime import timedelta
from typing import Any

from sqlalchemy import Connection, Engine
from sqlalchemy.dialects.postgresql import insert

from lotwatcher.db.models import ApiCacheEntry
from lotwatcher.db.session import transaction
from lotwatcher.sources.clock import Clock

# Returned by get() when there is no fresh entry. (None can't be used: null is valid JSON.)
MISS = object()


def cache_key(source: str, path: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Fingerprint of a request, the same whatever order the parameters are in.

    Callers must pass parameters without secrets; the returned request copy is stored as is.
    """
    request = {"source": source, "path": path, "params": params}
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest(), request


class ResponseCache:
    def __init__(self, bind: Engine | Connection, clock: Clock):
        self._bind = bind
        self._clock = clock

    def get(self, key: str) -> Any:
        with transaction(self._bind) as session:
            entry = session.get(ApiCacheEntry, key)
            if entry is None or (
                entry.expires_at is not None and entry.expires_at <= self._clock.now()
            ):
                return MISS
            return entry.response

    def put(
        self, key: str, source: str, request: dict[str, Any], response: Any, ttl: timedelta | None
    ) -> None:
        """Save a successful response. ttl=None means it never expires."""
        now = self._clock.now()
        values = {
            "source": source,
            "request": request,
            "response": response,
            "fetched_at": now,
            "expires_at": None if ttl is None else now + ttl,
        }
        statement = insert(ApiCacheEntry).values(cache_key=key, **values)
        statement = statement.on_conflict_do_update(index_elements=["cache_key"], set_=values)
        with transaction(self._bind) as session:
            session.execute(statement)
