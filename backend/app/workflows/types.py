"""Workflow/activity payloads. Plain dataclasses of strings and ints: safe to import inside the
Temporal workflow sandbox and serialised as JSON by the default data converter."""

from dataclasses import dataclass

BOOKED = "BOOKED"
REJECTED = "REJECTED"
FAILED = "FAILED"


@dataclass(frozen=True)
class ExecutionRef:
    workflow_id: str
    firm_id: str
    workflow_type: str
    entity_type: str
    entity_id: str


@dataclass(frozen=True)
class ExecutionResult:
    workflow_id: str
    succeeded: bool
    result: str
    error: str | None = None


@dataclass(frozen=True)
class HandoffInput:
    firm_id: str
    escalation_id: str


@dataclass(frozen=True)
class HandoffPlan:
    priority: str
    reason_text: str
    ack_minutes: int
    max_reminders: int
    delivery_attempts: int
    reviewer_role: str
    fallback_role: str
    status: str


@dataclass(frozen=True)
class NotifyInput:
    firm_id: str
    escalation_id: str
    recipient_role: str
    kind: str
    dedupe_key: str
    message: str
    # Notifications that use the reviewer channel go through the (fallible) notifier adapter.
    via_reviewer_channel: bool = True


@dataclass(frozen=True)
class HandoffFailure:
    firm_id: str
    escalation_id: str
    fallback_role: str
    error: str


@dataclass(frozen=True)
class BookingInput:
    firm_id: str
    intake_id: str
    slot_id: str
    idempotency_key: str
    actor_type: str
    actor_id: str
    attempt: int = 1


@dataclass(frozen=True)
class BookingOutcome:
    status: str  # BOOKED | REJECTED | FAILED
    appointment_id: str | None = None
    replayed: bool = False
    error_code: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class PostCallInput:
    firm_id: str
    call_session_id: str


@dataclass(frozen=True)
class PostCallPlan:
    firm_id: str
    intake_id: str | None
    intake_status: str | None
    follow_up_minutes: int
