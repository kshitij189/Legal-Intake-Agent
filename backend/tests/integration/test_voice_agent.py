"""The LiveKit agent runs the same conversation code path as text mode.

Uses LiveKit's text-mode session harness (no audio, no room): user input goes through
`IntakeVoiceAgent.llm_node` into `IntakeConversation` with a scripted model and the real database.
"""

import asyncio

from livekit.agents import AgentSession, ChatContext
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agent.conversation import IntakeConversation
from app.agent.voice_agent import (
    NOT_HEARD_REPLY,
    ConversationOwnedLLM,
    IntakeVoiceAgent,
    latest_caller_text,
)
from app.models import CallSession, Escalation
from app.models.enums import SessionChannel
from tests.fakes import ScriptedLLM, call, say, tools
from tests.helpers import NOW
from tests.integration.conftest import SeededFirm

Demo = tuple[sessionmaker[Session], SeededFirm]


def make_agent(demo: Demo, llm: ScriptedLLM) -> IntakeVoiceAgent:
    sessions, firm = demo
    convo = IntakeConversation(
        llm=llm,
        sessions=sessions,
        firm_id=firm.firm_id,
        clock=lambda: NOW,
        channel=SessionChannel.VOICE,
    )
    return IntakeVoiceAgent(convo)


async def run_turns(agent: IntakeVoiceAgent, *turns: str) -> list[str]:
    replies: list[str] = []
    async with AgentSession(llm=ConversationOwnedLLM()) as session:
        await session.start(agent)
        for turn in turns:
            result = await session.run(user_input=turn)
            replies.extend(
                e.item.text_content or ""
                for e in result.events
                if e.type == "message" and e.item.role == "assistant"
            )
    return replies


def test_voice_turn_reaches_the_conversation_and_database(committed_demo: Demo) -> None:
    llm = ScriptedLLM(
        [
            tools(
                call("create_draft_intake"),
                call(
                    "save_intake_answer",
                    field_key="reported_deadline",
                    status="REPORTED_BY_CALLER",
                    value="2026-10-13",
                ),
            ),
            say("I've flagged the deadline for the team's prompt review."),
        ]
    )
    agent = make_agent(committed_demo, llm)
    replies = asyncio.run(run_turns(agent, "I have to respond to a notice by tomorrow."))

    # The greeting may be spoken within the same run; the caller's turn gets the scripted reply.
    assert "I've flagged the deadline for the team's prompt review." in replies
    with committed_demo[0]() as s:
        call_session = s.scalars(select(CallSession)).one()
        assert call_session.channel is SessionChannel.VOICE
        assert s.scalars(select(Escalation.rule_id)).all() == ["imminent_deadline"]


def test_latest_caller_text_picks_the_last_user_message() -> None:
    ctx = ChatContext.empty()
    ctx.add_message(role="user", content="first")
    ctx.add_message(role="assistant", content="reply")
    ctx.add_message(role="user", content="  second  ")
    assert latest_caller_text(ctx) == "second"
    assert latest_caller_text(ChatContext.empty()) == ""


def test_empty_transcript_asks_the_caller_to_repeat(committed_demo: Demo) -> None:
    agent = make_agent(committed_demo, ScriptedLLM([]))

    async def run() -> list[str]:
        async with AgentSession(llm=ConversationOwnedLLM()) as session:
            await session.start(agent)
            await agent.ready.wait()
            chunks = [c async for c in agent.llm_node(ChatContext.empty(), [], None)]
        return [c for c in chunks if isinstance(c, str)]

    assert asyncio.run(run()) == [NOT_HEARD_REPLY]
