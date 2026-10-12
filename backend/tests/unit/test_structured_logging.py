"""JSON log lines keep `extra` fields and the request correlation id (Phase 8)."""

import json
import logging

from app.observability.logging import JsonFormatter, request_id


def record(**extra: object) -> logging.LogRecord:
    rec = logging.makeLogRecord(
        {"name": "app.test", "levelname": "WARNING", "msg": "reply_blocked"}
    )
    rec.__dict__.update(extra)
    return rec


def test_extras_become_json_fields() -> None:
    line = json.loads(JsonFormatter().format(record(violations=["false_booking_confirmation"])))
    assert line["event"] == "reply_blocked"
    assert line["violations"] == ["false_booking_confirmation"]
    assert line["level"] == "WARNING" and "ts" in line and "request_id" not in line


def test_request_id_is_attached() -> None:
    token = request_id.set("abc123def456")
    try:
        assert json.loads(JsonFormatter().format(record()))["request_id"] == "abc123def456"
    finally:
        request_id.reset(token)


def test_importing_the_app_does_not_reconfigure_logging() -> None:
    """Regression (Phase 8): configuring logging at import time hijacked the logging of every
    process that imports app.main (the audio evaluation runner), and all its calls errored.
    Logging is configured in the server's lifespan instead."""
    import importlib

    root = logging.getLogger()
    before = (list(root.handlers), root.level)
    importlib.reload(importlib.import_module("app.main"))
    assert (list(root.handlers), root.level) == before
