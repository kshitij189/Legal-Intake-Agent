"""Login throttling (Phase 8): bounded failures per account within a window."""

import pytest

from app.api.throttle import LoginThrottle, TooManyAttempts


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_blocks_after_max_failures_until_the_window_passes() -> None:
    clock = Clock()
    throttle = LoginThrottle(max_failures=3, window_seconds=60, clock=clock)
    key = ("firm", "a@example.com")
    for _ in range(3):
        throttle.check(key)
        throttle.failed(key)
    with pytest.raises(TooManyAttempts) as blocked:
        throttle.check(key)
    assert blocked.value.details["retry_after_seconds"] == 61
    throttle.check(("firm", "b@example.com"))  # other accounts are unaffected
    clock.now += 61
    throttle.check(key)  # the failures left the window


def test_success_resets_the_counter() -> None:
    throttle = LoginThrottle(max_failures=2, window_seconds=60, clock=Clock())
    key = ("firm", "a@example.com")
    throttle.failed(key)
    throttle.succeeded(key)
    throttle.failed(key)
    throttle.check(key)
