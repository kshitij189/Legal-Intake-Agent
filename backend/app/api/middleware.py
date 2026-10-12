"""Per-request correlation id and one structured access-log line (no bodies, no query values)."""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from app.observability.logging import request_id

logger = logging.getLogger("app.api.access")
HEADER = "X-Request-ID"


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def _correlate(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(HEADER, "")
        rid = incoming if 8 <= len(incoming) <= 64 and incoming.isascii() else uuid.uuid4().hex
        token = request_id.set(rid)
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers[HEADER] = rid
            return response
        finally:
            logger.info(
                "http_request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
            request_id.reset(token)
