"""Check the Sarvam key and the exact behaviour the agent needs, with three tiny requests.

Run once after putting SARVAM_API_KEY in .env (about 3 requests, a few thousand tokens):
    .\\.venv\\Scripts\\python scripts/check_sarvam.py

1. plain reply          - the key works and the model answers
2. tool call            - the model calls a tool with valid JSON arguments (the agent needs this)
3. tool result round    - the conversation continues after a tool result, as in a real turn

The key is never printed. Exit code 0 only if all three pass.
"""

import json
import sys

from app.agent.llm_client import LLMUnavailable
from app.agent.llm_sarvam import SarvamLLMClient
from app.config import get_settings

TOOL = {
    "type": "function",
    "function": {
        "name": "save_intake_answer",
        "description": "Save one answer from the caller.",
        "parameters": {
            "type": "object",
            "properties": {
                "field_key": {"type": "string"},
                "status": {"type": "string", "enum": ["REPORTED_BY_CALLER"]},
                "value": {"type": "string"},
            },
            "required": ["field_key", "status", "value"],
            "additionalProperties": False,
        },
    },
}
SYSTEM = {
    "role": "system",
    "content": "You are an intake assistant. Save every fact the caller states with the tool, "
    "then reply in one short sentence.",
}


def main() -> int:
    settings = get_settings()
    key = settings.sarvam_api_key.get_secret_value() if settings.sarvam_api_key else ""
    if not key or key == "replace-me":
        print("SARVAM_API_KEY is not set in .env (or is still 'replace-me').")
        return 2
    client = SarvamLLMClient(
        api_key=key,
        model=settings.sarvam_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=1,
        rate_limit_max_wait_seconds=10.0,
        base_url=settings.sarvam_base_url,
    )
    print(f"model {client.model} at {settings.sarvam_base_url}")
    results: list[tuple[str, bool, str]] = []

    def step(name: str, check: object) -> object:
        try:
            outcome = check()  # type: ignore[operator]
        except LLMUnavailable as exc:
            hint = {
                "auth_failed": "the key was rejected (wrong key, or a different header is needed)",
                "credits_exhausted": "no credits left on this key",
            }.get(exc.reason, "")
            results.append((name, False, f"{exc.reason} {hint}".strip()))
            return None
        results.append((name, True, str(outcome)))
        return outcome

    user = {"role": "user", "content": "Hi, my name is Priya Sharma."}
    step("plain reply", lambda: _plain(client, user))
    call = step("tool call", lambda: _tool_call(client, user))
    if call is not None:
        step("tool result round", lambda: _after_tool(client, user, call))  # type: ignore[arg-type]
    for name, passed, detail in results:
        print(f"{'PASS' if passed else 'FAIL'}  {name}: {detail}")
    ok = len(results) == 3 and all(p for _, p, _ in results)
    if ok:
        print(
            "\nAll good. To use it, add sarvam to LLM_PROVIDERS in .env, e.g. for a smooth voice "
            "session:\n  LLM_PROVIDERS=sarvam,gemini:gemini-3.5-flash-lite,groq:openai/gpt-oss-120b"
        )
    return 0 if ok else 1


def _plain(client: SarvamLLMClient, user: dict[str, str]) -> str:
    reply = client.complete([SYSTEM, user], [])
    if not reply.content:
        raise LLMUnavailable("empty_reply")
    return f"{reply.content[:80]!r} usage={reply.usage}"


def _tool_call(client: SarvamLLMClient, user: dict[str, str]) -> object:
    reply = client.complete([SYSTEM, user], [TOOL])
    if not reply.tool_calls:
        raise LLMUnavailable("no_tool_call_made")
    arguments = json.loads(reply.tool_calls[0].arguments)  # must be valid JSON
    if not {"field_key", "value"} <= set(arguments):
        raise LLMUnavailable(f"bad_tool_arguments {arguments}")
    return reply


def _after_tool(client: SarvamLLMClient, user: dict[str, str], call: object) -> str:
    first = call.tool_calls[0]  # type: ignore[attr-defined]
    messages = [
        SYSTEM,
        user,
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": first.id,
                    "type": "function",
                    "function": {"name": first.name, "arguments": first.arguments},
                }
            ],
        },
        {"role": "tool", "tool_call_id": first.id, "content": '{"ok": true}'},
    ]
    reply = client.complete(messages, [TOOL])
    if not (reply.content or reply.tool_calls):
        raise LLMUnavailable("empty_after_tool")
    return f"{(reply.content or '<more tool calls>')[:80]!r}"


if __name__ == "__main__":
    sys.exit(main())
