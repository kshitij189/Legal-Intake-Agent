"""LiveKit voice agent that delegates every reply to `IntakeConversation`.

LiveKit owns audio, VAD, turn-taking and interruptions. The conversation (LLM providers, tools,
omission check, reply guard, persistence) is exactly the code path tested in text mode.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any, Literal

from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, Agent, APIConnectOptions, llm
from livekit.agents.types import NOT_GIVEN, NotGivenOr

from app.agent.conversation import IntakeConversation

logger = logging.getLogger(__name__)

NOT_HEARD_REPLY = "Sorry, I didn't catch that. Could you say it again?"


class ConversationOwnedLLM(llm.LLM[Literal["metrics_collected"]]):
    """Marker LLM: replies come from `IntakeVoiceAgent.llm_node`, never from `chat()`.

    LiveKit skips reply generation entirely when a session has no LLM, so one must be set."""

    @property
    def model(self) -> str:
        return "intake-conversation"

    @property
    def provider(self) -> str:
        return "app"

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[Any] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
        tool_choice: NotGivenOr[Any] = NOT_GIVEN,
        extra_kwargs: NotGivenOr[dict[str, Any]] = NOT_GIVEN,
    ) -> llm.LLMStream:
        raise RuntimeError("replies are produced by IntakeVoiceAgent.llm_node")


def latest_caller_text(chat_ctx: llm.ChatContext) -> str:
    for item in reversed(chat_ctx.items):
        if isinstance(item, llm.ChatMessage) and item.role == "user":
            return (item.text_content or "").strip()
    return ""


class IntakeVoiceAgent(Agent):
    def __init__(self, conversation: IntakeConversation) -> None:
        super().__init__(instructions="Replies are produced by the intake conversation service.")
        self.conversation = conversation
        self._turn_lock = asyncio.Lock()
        # Set once the conversation (call session, prompt, tools) exists. A caller who speaks
        # before that must wait rather than reach an unstarted conversation.
        self.ready = asyncio.Event()

    async def on_enter(self) -> None:
        opening = await asyncio.to_thread(self.conversation.start)
        self.ready.set()
        try:
            # Not interruptible: the greeting carries the transcript disclosure, which the caller
            # must hear in full before the conversation continues.
            self.session.say(opening, allow_interruptions=False)
        except RuntimeError:
            logger.warning("greeting_not_spoken_session_closing")

    async def llm_node(
        self,
        chat_ctx: llm.ChatContext,
        tools: list[Any],
        model_settings: Any,
    ) -> AsyncIterator[str]:
        text = latest_caller_text(chat_ctx)
        await self.ready.wait()
        if self.conversation.ended:
            return
        if not text:
            yield NOT_HEARD_REPLY  # STT produced nothing usable: ask to repeat (blueprint §14)
            return
        # Turns are processed one at a time; LiveKit may start a new reply after a barge-in.
        async with self._turn_lock:
            reply = await asyncio.to_thread(self.conversation.respond, text)
        logger.info(
            "voice_turn processing_ms=%d",
            self.conversation.last_processing_ms or 0,
            extra={"processing_ms": self.conversation.last_processing_ms},
        )
        yield reply
