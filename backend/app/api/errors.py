"""Typed domain errors -> HTTP responses with a stable `{"error": {"code", "message"}}` body."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.domain.errors import (
    DomainError,
    InvalidTransition,
    NotFound,
    PermissionDenied,
    ValidationFailed,
)
from app.domain.staff_auth_service import AuthenticationFailed

_STATUS: list[tuple[type[DomainError], int]] = [
    (AuthenticationFailed, 401),  # before PermissionDenied (its base class)
    (PermissionDenied, 403),
    (NotFound, 404),
    (ValidationFailed, 422),
    (InvalidTransition, 409),
]


def http_status(error: DomainError) -> int:
    return next((status for cls, status in _STATUS if isinstance(error, cls)), 409)


def install(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=http_status(exc),
            content={"error": {"code": exc.code, "message": exc.message}},
        )
