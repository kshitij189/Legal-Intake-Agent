import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.agent.llm_client import LLMResponse, LLMUnavailable, ToolCall

Step = LLMResponse | LLMUnavailable | Callable[[Sequence[Mapping[str, Any]]], LLMResponse]


def call(name: str, call_id: str | None = None, **arguments: Any) -> ToolCall:
    return ToolCall(id=call_id or f"call_{name}", name=name, arguments=json.dumps(arguments))


def tools(*calls: ToolCall) -> LLMResponse:
    return LLMResponse(content=None, tool_calls=calls)


def say(text: str) -> LLMResponse:
    return LLMResponse(content=text)


class ScriptedLLM:
    """Deterministic stand-in for the model: returns scripted steps in order and records what it
    was sent. A step may be a callable that builds the response from the message history."""

    def __init__(self, steps: Sequence[Step]) -> None:
        self._steps = list(steps)
        self.requests: list[list[dict[str, Any]]] = []
        self.tools_seen: list[list[dict[str, Any]]] = []

    def complete(
        self, messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]]
    ) -> LLMResponse:
        self.requests.append([dict(m) for m in messages])
        self.tools_seen.append([dict(t) for t in tools])
        if not self._steps:
            raise AssertionError("ScriptedLLM ran out of steps")
        step = self._steps.pop(0)
        if isinstance(step, LLMUnavailable):
            raise step
        if callable(step) and not isinstance(step, LLMResponse):
            return step(messages)
        return step

    def tool_results(self) -> list[dict[str, Any]]:
        """Parsed tool results from the latest request."""
        return [json.loads(m["content"]) for m in self.requests[-1] if m["role"] == "tool"]
