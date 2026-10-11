"""Typed domain errors with stable codes. Tool and API layers map these to structured results."""

from typing import Any, ClassVar


class DomainError(Exception):
    code: ClassVar[str] = "DOMAIN_ERROR"
    retryable: ClassVar[bool] = False

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class NotFound(DomainError):
    code = "NOT_FOUND"


class ValidationFailed(DomainError):
    code = "VALIDATION_FAILED"


class ConfigurationError(DomainError):
    code = "CONFIGURATION_ERROR"


class InvalidTransition(DomainError):
    code = "INVALID_TRANSITION"


class PermissionDenied(DomainError):
    code = "PERMISSION_DENIED"


class SlotUnavailable(DomainError):
    code = "SLOT_UNAVAILABLE"


class AlreadyBooked(DomainError):
    code = "ALREADY_BOOKED"


class IdempotencyConflict(DomainError):
    code = "IDEMPOTENCY_CONFLICT"


class RuleNotMatched(DomainError):
    code = "ESCALATION_RULE_NOT_MATCHED"
