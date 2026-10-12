"""Login throttling: after MAX_FAILURES failed sign-ins for one (firm, email, client) within
WINDOW, further attempts are refused until the oldest failure leaves the window.

In-process memory: enough for the single-instance local demo; a multi-instance deployment would
keep these counters in a shared store (documented limitation)."""

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable

from app.domain.errors import DomainError

MAX_FAILURES = 5
WINDOW_SECONDS = 15 * 60


class TooManyAttempts(DomainError):
    code = "TOO_MANY_ATTEMPTS"


class LoginThrottle:
    def __init__(
        self,
        max_failures: int = MAX_FAILURES,
        window_seconds: float = WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_failures
        self._window = window_seconds
        self._clock = clock
        self._failures: dict[tuple[str, ...], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _recent(self, key: tuple[str, ...]) -> deque[float]:
        failures = self._failures[key]
        cutoff = self._clock() - self._window
        while failures and failures[0] <= cutoff:
            failures.popleft()
        return failures

    def check(self, key: tuple[str, ...]) -> None:
        with self._lock:
            failures = self._recent(key)
            if len(failures) >= self._max:
                retry = int(failures[0] + self._window - self._clock()) + 1
                raise TooManyAttempts(
                    "too many failed sign-in attempts; try again later", retry_after_seconds=retry
                )

    def failed(self, key: tuple[str, ...]) -> None:
        with self._lock:
            self._recent(key).append(self._clock())

    def succeeded(self, key: tuple[str, ...]) -> None:
        with self._lock:
            self._failures.pop(key, None)
