import logging
import re
import time
import uuid
from contextvars import ContextVar

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"

# Accept a caller-supplied id only if it is short and log-safe; otherwise mint one.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")

# Health probes run every few seconds; keep them out of INFO-level access logs.
_QUIET_PATHS = frozenset({"/health"})

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

access_logger = logging.getLogger("http.access")


def get_request_id() -> str | None:
    return _request_id.get()


class RequestContextMiddleware:
    """Pure ASGI middleware that, per HTTP request:

    - reads X-Request-ID (or mints one), exposes it via get_request_id() and
      request.state.request_id, and echoes it on the response;
    - writes one structured `http.access` log record with method, path, status and
      duration.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = next(
            (
                value.decode("latin-1")
                for name, value in scope["headers"]
                if name.lower() == REQUEST_ID_HEADER.lower().encode()
            ),
            None,
        )
        request_id = (
            incoming if incoming and _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        )
        scope.setdefault("state", {})["request_id"] = request_id
        token = _request_id.set(request_id)
        started = time.perf_counter()
        status_code = 500  # if the app raises before responding

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            path = scope["path"]
            access_logger.log(
                logging.DEBUG if path in _QUIET_PATHS else logging.INFO,
                "%s %s %s",
                scope["method"],
                path,
                status_code,
                extra={
                    "method": scope["method"],
                    "path": path,
                    "query": scope.get("query_string", b"").decode("latin-1") or None,
                    "status": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                    "client": (scope.get("client") or (None,))[0],
                },
            )
            _request_id.reset(token)
