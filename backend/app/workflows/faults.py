"""Explicit, opt-in fault injection for tests and the failure demo (never enabled by default).

CALENDAR_FAULT_INJECTION:
  timeout_after_commit:N  - booking attempts 1..N commit, then raise a simulated timeout
  hang_after_commit:N     - booking attempts 1..N commit, then hang (worker killed / timed out)
NOTIFIER_FAULT_INJECTION:
  unavailable             - every reviewer-channel delivery fails
  fail_first:N            - activity attempts 1..N fail, later ones succeed
"""

from dataclasses import dataclass

CALENDAR_MODES = {"timeout_after_commit", "hang_after_commit"}
NOTIFIER_MODES = {"unavailable", "fail_first"}


@dataclass(frozen=True)
class FaultSpec:
    mode: str
    count: int = 0

    def applies_to_attempt(self, attempt: int) -> bool:
        return self.mode == "unavailable" or attempt <= self.count


def parse_fault(spec: str | None, allowed: set[str]) -> FaultSpec | None:
    if not spec:
        return None
    mode, _, count = spec.strip().partition(":")
    if mode not in allowed:
        raise ValueError(f"unknown fault mode {mode!r}; expected one of {sorted(allowed)}")
    return FaultSpec(mode=mode, count=int(count) if count else 0)
