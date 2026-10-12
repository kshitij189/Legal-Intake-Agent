"""Structured (JSON-lines) logging for the API, the Temporal worker and the voice worker.

`extra={...}` fields become top-level JSON keys, so events such as `reply_blocked` or
`llm_model_failed` keep their correlation ids and reasons. Never pass transcript text or caller
contact details in log extras (CLAUDE.md section 10).
"""

import contextvars
import json
import logging
from datetime import UTC, datetime
from typing import Any

# Correlation id of the current API request (set by the request middleware).
request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)

_STANDARD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        rid = request_id.get()
        if rid:
            entry["request_id"] = rid
        entry.update(
            {
                k: v
                for k, v in record.__dict__.items()
                if k not in _STANDARD and not k.startswith("_")
            }
        )
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """JSON lines on stderr for the root logger (idempotent)."""
    root = logging.getLogger()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
    root.setLevel(level)
