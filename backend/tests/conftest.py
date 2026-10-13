"""Suite-wide settings. Tests never send traces to a real Langfuse server, whatever .env says;
tracing behaviour is tested with an injected fake client (tests/unit/test_tracing.py)."""

import os

os.environ["LANGFUSE_ENABLED"] = "false"
