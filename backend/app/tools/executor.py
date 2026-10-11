"""Validated tool execution: parse -> validate -> run in one transaction -> verify -> result.

Every model tool call goes through `ToolExecutor.execute`. Business rules stay in the domain
services; this layer adds argument validation, session scoping, commit boundaries, post-commit
verification and auditing of rejected or failed calls.
"""

import json
import logging
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.domain.audit import record_audit
from app.domain.call_session_service import get_call_session
from app.domain.errors import DomainError
from app.models.enums import AuditOutcome
from app.tools.context import ToolContext
from app.tools.schemas import ToolError, ToolInput, ToolResult

logger = logging.getLogger(__name__)

Handler = Callable[[Session, ToolContext, Any], dict[str, Any]]
Verifier = Callable[[Session, ToolContext, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[ToolInput]
    handler: Handler
    # Runs after commit in a fresh session; the result is reported only if this succeeds.
    verify: Verifier | None = None


class ToolExecutor:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        ctx: ToolContext,
        registry: Mapping[str, ToolSpec],
    ) -> None:
        self._sessions = session_factory
        self._ctx = ctx
        self._registry = registry

    def execute(self, name: str, raw_arguments: str | None) -> ToolResult:
        started = time.perf_counter()
        result = self._execute(name, raw_arguments)
        logger.info(
            "tool_call",
            extra={
                "tool": name,
                "ok": result.ok,
                "error_code": result.error.code if result.error else None,
                "call_session_id": str(self._ctx.call_session_id),
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return result

    def _execute(self, name: str, raw_arguments: str | None) -> ToolResult:
        spec = self._registry.get(name)
        if spec is None:
            return self._reject(name, ToolError("UNKNOWN_TOOL", f"there is no tool named {name!r}"))
        try:
            args = spec.input_model.model_validate(json.loads(raw_arguments or "{}"))
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            return self._reject(
                name,
                ToolError(
                    "INVALID_ARGUMENTS", "tool arguments failed validation", details=_why(exc)
                ),
            )

        try:
            with self._sessions() as session, session.begin():
                self._refresh_context(session)
                data = spec.handler(session, self._ctx, args)
            if spec.verify is not None:
                with self._sessions() as session:
                    data = spec.verify(session, self._ctx, data)
        except DomainError as exc:
            return self._reject(
                name,
                ToolError(exc.code, exc.message, exc.retryable, _safe_details(exc.details)),
            )
        except Exception:  # database or programming error: never let it reach the caller as success
            logger.exception("tool_internal_error", extra={"tool": name})
            return self._reject(
                name,
                ToolError(
                    "INTERNAL_ERROR",
                    "the operation could not be completed; nothing should be confirmed to the"
                    " caller",
                ),
                outcome=AuditOutcome.FAILURE,
            )
        return ToolResult.success(data)

    def _refresh_context(self, session: Session) -> None:
        call = get_call_session(
            session, firm_id=self._ctx.firm_id, call_session_id=self._ctx.call_session_id
        )
        self._ctx.intake_id = call.intake_id

    def _reject(
        self, name: str, error: ToolError, outcome: AuditOutcome = AuditOutcome.REJECTED
    ) -> ToolResult:
        """Audit the rejected call in its own transaction (the work itself was rolled back)."""
        try:
            with self._sessions() as session, session.begin():
                record_audit(
                    session,
                    firm_id=self._ctx.firm_id,
                    actor=self._ctx.actor,
                    action=f"tool.{name}"[:64],
                    entity_type="call_session",
                    entity_id=self._ctx.call_session_id,
                    outcome=outcome,
                    details={"error_code": error.code},
                )
        except SQLAlchemyError:
            logger.exception("tool_audit_failed", extra={"tool": name})
        return ToolResult.failure(error)


def _why(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, ValidationError):
        return {
            "errors": [
                {"field": ".".join(str(p) for p in e["loc"]), "problem": e["msg"]}
                for e in exc.errors()
            ]
        }
    return {"errors": [{"problem": "arguments must be a JSON object"}]}


def _safe_details(details: Mapping[str, Any]) -> dict[str, Any]:
    return {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in details.items()}
