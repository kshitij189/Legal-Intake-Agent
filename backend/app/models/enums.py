from enum import StrEnum


class StaffRole(StrEnum):
    LAWYER = "LAWYER"
    INTAKE_COORDINATOR = "INTAKE_COORDINATOR"
    ADMINISTRATOR = "ADMINISTRATOR"


class SlotStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    BOOKED = "BOOKED"
    BLOCKED = "BLOCKED"


class IntakeStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    INCOMPLETE = "INCOMPLETE"
    PENDING_REVIEW = "PENDING_REVIEW"
    CLOSED = "CLOSED"


class FieldStatus(StrEnum):
    MISSING = "MISSING"
    REPORTED_BY_CALLER = "REPORTED_BY_CALLER"
    CONFIRMED_BY_CALLER = "CONFIRMED_BY_CALLER"
    DECLINED_BY_CALLER = "DECLINED_BY_CALLER"
    PENDING_HUMAN_REVIEW = "PENDING_HUMAN_REVIEW"
    VERIFIED_BY_SYSTEM = "VERIFIED_BY_SYSTEM"


class AppointmentStatus(StrEnum):
    BOOKED = "BOOKED"


class EscalationStatus(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    FAILED = "FAILED"
    RESOLVED = "RESOLVED"


class EscalationReason(StrEnum):
    CONFIGURED_RULE = "CONFIGURED_RULE"
    UNABLE_TO_COMPLETE = "UNABLE_TO_COMPLETE"


class Priority(StrEnum):
    URGENT = "URGENT"
    HIGH = "HIGH"
    NORMAL = "NORMAL"


class ActorType(StrEnum):
    AGENT = "AGENT"
    STAFF = "STAFF"
    SYSTEM = "SYSTEM"


class SessionChannel(StrEnum):
    TEXT = "TEXT"
    VOICE = "VOICE"


class CallSessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    DISCONNECTED = "DISCONNECTED"
    FAILED = "FAILED"


class Speaker(StrEnum):
    CALLER = "CALLER"
    AGENT = "AGENT"


class AuditOutcome(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    REJECTED = "REJECTED"
