"""Text conversation loop: caller text -> LLM -> validated tools -> reply.

The voice runtime (Phase 4) will drive the same loop with transcribed speech.
"""

import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.agent import prompts
from app.agent.llm_client import PROVIDER_STATE_KEY, LLMClient, LLMResponse, LLMUnavailable
from app.agent.tool_registry import REGISTRY, tool_definitions
from app.domain import escalation_service, intake_service
from app.domain.actors import Actor
from app.domain.call_session_service import (
    end_call_session,
    get_call_session,
    start_call_session,
)
from app.domain.firm_config_service import firm_today, get_active_firm, load_config
from app.models.enums import CallSessionStatus, EscalationReason, IntakeStatus, SessionChannel
from app.tools.context import ToolContext
from app.tools.escalation_tools import unable_escalation_key
from app.tools.executor import ToolExecutor

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8
RETRY_PROMPT = "I'm sorry, I didn't manage to complete that. Could you say that again?"
FAILURE_FLAGGED = (
    "I'm sorry, I'm having a technical problem and can't continue right now. I've saved what "
    "you've told me so far and flagged your enquiry for the firm's team to review. Nobody has "
    "reviewed it yet, so if your matter is urgent, please contact the firm directly."
)
FAILURE_NOT_FLAGGED = (
    "I'm sorry, I'm having a technical problem and can't continue right now. "
    "Please contact the firm directly."
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _with_provider_state(message: dict[str, Any], response: LLMResponse) -> dict[str, Any]:
    """Keep provider-only data (e.g. Gemini thought signatures) for that provider's next call."""
    if response.provider_state is not None:
        message[PROVIDER_STATE_KEY] = {response.provider: response.provider_state}
    return message


@dataclass(frozen=True)
class TraceEntry:
    tool: str
    arguments: str
    result: str


class ConversationEnded(Exception):
    pass


@dataclass
class IntakeConversation:
    llm: LLMClient
    sessions: sessionmaker[Session]
    firm_id: uuid.UUID
    actor: Actor = field(default_factory=Actor.agent)
    clock: Callable[[], datetime] = _utc_now
    channel: SessionChannel = SessionChannel.TEXT
    max_tool_rounds: int = MAX_TOOL_ROUNDS

    call_session_id: uuid.UUID | None = field(default=None, init=False)
    ended: bool = field(default=False, init=False)
    trace: list[TraceEntry] = field(default_factory=list, init=False)
    providers_used: list[str] = field(default_factory=list, init=False)  # one per model response
    _messages: list[dict[str, Any]] = field(default_factory=list, init=False)
    _tools: list[dict[str, Any]] = field(default_factory=list, init=False)
    _executor: ToolExecutor | None = field(default=None, init=False)

    def start(self) -> str:
        with self.sessions() as session, session.begin():
            firm = get_active_firm(session, self.firm_id)
            config = load_config(firm)
            call = start_call_session(
                session, firm_id=self.firm_id, actor=self.actor, channel=self.channel
            )
            self.call_session_id = call.id
            system = prompts.system_prompt(
                firm.name, firm_today(firm, self.clock()), firm.timezone, config
            )
            opening = prompts.greeting(firm.name)
        ctx = ToolContext(
            firm_id=self.firm_id, call_session_id=call.id, actor=self.actor, clock=self.clock
        )
        self._executor = ToolExecutor(self.sessions, ctx, REGISTRY)
        self._tools = tool_definitions(config)
        self._messages = [
            {"role": "system", "content": system},
            {"role": "assistant", "content": opening},
        ]
        return opening

    def respond(self, caller_text: str) -> str:
        if self.ended or self._executor is None:
            raise ConversationEnded("the conversation is not active")
        self._messages.append({"role": "user", "content": caller_text})

        for _ in range(self.max_tool_rounds):
            try:
                response = self.llm.complete(self._messages, self._tools)
            except LLMUnavailable as exc:
                logger.error("llm_unavailable", extra={"reason": exc.reason})
                return self._fail(exc.reason)

            self.providers_used.append(response.provider or "unknown")
            if not response.tool_calls:
                reply = (response.content or "").strip() or RETRY_PROMPT
                self._messages.append(
                    _with_provider_state({"role": "assistant", "content": reply}, response)
                )
                return reply

            self._messages.append(
                _with_provider_state(
                    {
                        "role": "assistant",
                        "content": response.content,
                        "tool_calls": [
                            {
                                "id": call.id,
                                "type": "function",
                                "function": {"name": call.name, "arguments": call.arguments},
                            }
                            for call in response.tool_calls
                        ],
                    },
                    response,
                )
            )
            # Sequential on purpose: later calls may depend on earlier results.
            for call in response.tool_calls:
                result = self._executor.execute(call.name, call.arguments).to_json()
                self.trace.append(TraceEntry(call.name, call.arguments, result))
                self._messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

        logger.warning(
            "tool_round_limit_reached", extra={"call_session_id": str(self.call_session_id)}
        )
        self._messages.append({"role": "assistant", "content": RETRY_PROMPT})
        return RETRY_PROMPT

    def end(self, *, disconnected: bool = False) -> CallSessionStatus:
        """Close the session. An unfinished intake is kept and marked INCOMPLETE."""
        if self.ended:
            raise ConversationEnded("the conversation has already ended")
        assert self.call_session_id is not None
        with self.sessions() as session, session.begin():
            call = get_call_session(
                session, firm_id=self.firm_id, call_session_id=self.call_session_id
            )
            unfinished = False
            if call.intake_id is not None:
                intake = intake_service.get_intake(
                    session, firm_id=self.firm_id, intake_id=call.intake_id
                )
                unfinished = intake.status is IntakeStatus.IN_PROGRESS
            status = (
                CallSessionStatus.DISCONNECTED
                if disconnected or unfinished
                else CallSessionStatus.COMPLETED
            )
            end_call_session(
                session,
                firm_id=self.firm_id,
                call_session_id=self.call_session_id,
                actor=self.actor,
                status=status,
                reason="caller_disconnected" if disconnected else None,
                now=self.clock(),
            )
        self.ended = True
        return status

    def _fail(self, reason: str) -> str:
        """LLM fallback: keep partial state, queue a human review, end the session FAILED, and
        tell the caller only what was actually persisted."""
        assert self.call_session_id is not None
        escalation_id: uuid.UUID | None = None
        try:
            with self.sessions() as session, session.begin():
                call = get_call_session(
                    session, firm_id=self.firm_id, call_session_id=self.call_session_id
                )
                if call.intake_id is not None:
                    escalation_id = escalation_service.create_escalation(
                        session,
                        firm_id=self.firm_id,
                        actor=self.actor,
                        intake_id=call.intake_id,
                        reason_code=EscalationReason.UNABLE_TO_COMPLETE,
                        description=(
                            f"Automated intake stopped: language model unavailable ({reason})."
                        ),
                        idempotency_key=unable_escalation_key(call.id),
                        now=self.clock(),
                    ).escalation.id
                end_call_session(
                    session,
                    firm_id=self.firm_id,
                    call_session_id=call.id,
                    actor=self.actor,
                    status=CallSessionStatus.FAILED,
                    reason=f"llm_unavailable:{reason}",
                    now=self.clock(),
                )
            if escalation_id is not None:
                with self.sessions() as session:  # confirm it is really stored
                    escalation_service.get_escalation(
                        session, firm_id=self.firm_id, escalation_id=escalation_id
                    )
        except Exception:
            logger.exception("llm_fallback_failed")
            escalation_id = None
        self.ended = True
        return FAILURE_FLAGGED if escalation_id is not None else FAILURE_NOT_FLAGGED

    def transcript(self) -> str:
        """Debug view of the message history (system prompt omitted)."""
        return json.dumps(self._messages[1:], indent=2, default=str)
