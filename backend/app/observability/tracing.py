"""Langfuse tracing behind a small adapter (the only module that imports the Langfuse SDK).

One trace per conversation turn, grouped into a Langfuse session per call session:

    intake.turn (agent)
      ├─ llm <provider:model> (generation: model, tokens, latency, failures per chain entry)
      ├─ tool <name> (tool: ok / error code)
      └─ reply_guard / omission_check (guardrail: what was blocked or nudged)

Tracing is off unless LANGFUSE_ENABLED and both keys are set; then every call here is a no-op.
Content (caller text, model messages, tool arguments) is sent only with LANGFUSE_CAPTURE_CONTENT.
Tracing must never break a conversation: SDK errors are logged and swallowed.
Spans are exported in the background (every few seconds) and on process exit (the SDK registers
an atexit shutdown), so nothing blocks the call; flush() is only for explicit checkpoints.
"""

import contextlib
import logging
from collections.abc import Iterator
from functools import lru_cache
from typing import Any, Literal, Protocol

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

ObservationType = Literal["span", "agent", "generation", "tool", "guardrail"]
Level = Literal["DEBUG", "DEFAULT", "WARNING", "ERROR"]


class Observation(Protocol):
    def update(self, **fields: Any) -> Any: ...


class _NoOp:
    def update(self, **fields: Any) -> None:
        return None


_NOOP = _NoOp()


class Tracer:
    def __init__(self, client: Any = None, *, capture_content: bool = False) -> None:
        self._client = client
        self.capture_content = capture_content

    @property
    def enabled(self) -> bool:
        return self._client is not None

    def content(self, value: Any) -> Any:
        """`value` if content capture is on; otherwise only its size."""
        if self.capture_content or value is None:
            return value
        return {"redacted": True, "chars": len(str(value))}

    @contextlib.contextmanager
    def session(
        self, session_id: str, *, trace_name: str, tags: list[str], metadata: dict[str, str]
    ) -> Iterator[None]:
        """Attributes shared by every observation in the block (Langfuse session = call)."""
        if self._client is None:
            yield
            return
        try:
            from langfuse import propagate_attributes

            manager = propagate_attributes(
                session_id=session_id, trace_name=trace_name, tags=tags, metadata=metadata
            )
            manager.__enter__()
        except Exception:
            logger.warning("tracing_failed", exc_info=True)
            yield
            return
        try:
            yield
        finally:
            with contextlib.suppress(Exception):
                manager.__exit__(None, None, None)

    @contextlib.contextmanager
    def observe(
        self, name: str, *, as_type: ObservationType = "span", **fields: Any
    ) -> Iterator[Observation]:
        if self._client is None:
            yield _NOOP
            return
        try:
            manager = self._client.start_as_current_observation(
                name=name, as_type=as_type, **fields
            )
            observation = manager.__enter__()
        except Exception:
            logger.warning("tracing_failed", exc_info=True)
            yield _NOOP
            return
        safe = _Safe(observation)
        try:
            yield safe
        except BaseException as exc:
            safe.update(level="ERROR", status_message=type(exc).__name__)
            with contextlib.suppress(Exception):
                manager.__exit__(type(exc), exc, exc.__traceback__)
            raise
        with contextlib.suppress(Exception):
            manager.__exit__(None, None, None)

    def flush(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.flush()


class _Safe:
    """Wraps a Langfuse observation so a failing update never reaches the conversation."""

    def __init__(self, observation: Any) -> None:
        self._observation = observation

    def update(self, **fields: Any) -> None:
        try:
            self._observation.update(**fields)
        except Exception:
            logger.warning("tracing_failed", exc_info=True)


def build_tracer(settings: Settings) -> Tracer:
    if not (
        settings.langfuse_enabled
        and settings.langfuse_public_key
        and settings.langfuse_secret_key is not None
    ):
        return Tracer()
    try:
        from langfuse import Langfuse

        client = Langfuse(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key.get_secret_value(),
            base_url=settings.langfuse_base_url,
            environment=settings.langfuse_environment,
            # Export happens off the call path; a local server under load answered slower than the
            # SDK's 5 s default and batches were dropped (measured 2026-10-13).
            timeout=20,
        )
    except Exception:
        logger.warning("tracing_disabled_client_failed", exc_info=True)
        return Tracer()
    return Tracer(client, capture_content=settings.langfuse_capture_content)


@lru_cache
def get_tracer() -> Tracer:
    return build_tracer(get_settings())
