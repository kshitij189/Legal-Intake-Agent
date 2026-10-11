"""Interactive text intake conversation with the real LLM (Groq) and the real database.

Usage: .venv\\Scripts\\python scripts/text_chat.py
Commands: /status (stored intake), /trace (tool calls), /quit (hang up).
Uses fictional data only - messages are sent to Groq.
"""

import json
import logging

from sqlalchemy import select

from app.agent.conversation import IntakeConversation
from app.agent.llm_client import build_llm_client
from app.config import get_settings
from app.db.session import make_engine, make_session_factory
from app.models import CallSession, Firm, IntakeRecord


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    settings = get_settings()
    sessions = make_session_factory(make_engine(settings.database_url))
    with sessions() as s:
        firm_id = s.scalars(select(Firm.id).where(Firm.slug == settings.demo_firm_slug)).one()

    convo = IntakeConversation(llm=build_llm_client(settings), sessions=sessions, firm_id=firm_id)
    print(f"[providers {settings.llm_providers}]")
    print(f"Agent: {convo.start()}")
    while not convo.ended:
        try:
            text = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            text = "/quit"
        if text == "/quit":
            print(f"[session ended: {convo.end(disconnected=True)}]")
            break
        if text == "/trace":
            for entry in convo.trace:
                print(f"  {entry.tool}({entry.arguments}) -> {entry.result}")
            continue
        if text == "/status":
            with sessions() as s:
                call = s.get_one(CallSession, convo.call_session_id)
                intake = s.get(IntakeRecord, call.intake_id) if call.intake_id else None
                print(
                    json.dumps(
                        {
                            "session": call.status,
                            "intake": intake.reference if intake else None,
                            "intake_status": intake.status if intake else None,
                            "fields": intake.fields if intake else None,
                        },
                        indent=2,
                        default=str,
                    )
                )
            continue
        if text:
            print(f"Agent: {convo.respond(text)}")
    if convo.ended and convo.call_session_id:
        print("[conversation closed]")


if __name__ == "__main__":
    main()
