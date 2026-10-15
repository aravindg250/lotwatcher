from lotwatcher.sources.clock import Clock


class RateLimiter:
    """Keeps calls to one source at least 1 / calls_per_second seconds apart."""

    def __init__(self, calls_per_second: float, clock: Clock):
        self._gap = 1.0 / calls_per_second
        self._clock = clock
        self._last_call: float | None = None

    def wait(self) -> None:
        if self._last_call is not None:
            remaining = self._last_call + self._gap - self._clock.monotonic()
            if remaining > 0:
                self._clock.sleep(remaining)
        self._last_call = self._clock.monotonic()
