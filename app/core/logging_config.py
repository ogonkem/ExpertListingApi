import json
import logging
from datetime import UTC, datetime
from typing import Any

from app.core.request_id import get_request_id

HANDLER_NAME = "expert-listing"

# Attributes every LogRecord has; anything else was passed via `extra=` and is emitted.
# `color_message` is uvicorn's ANSI-coloured duplicate of the message.
_STANDARD_ATTRS = frozenset(
    set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__)
    | {"message", "asctime", "request_id", "taskName", "color_message"}
)


class RequestIdFilter(logging.Filter):
    """Adds `request_id` to every record ("-" outside a request)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = get_request_id() or "-"
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line: timestamp, level, logger, message, request_id, env,
    any `extra=` fields, and the formatted exception if there is one."""

    def __init__(self, env: str) -> None:
        super().__init__()
        self.env = env

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "env": self.env,
        }
        payload |= {
            key: value for key, value in record.__dict__.items() if key not in _STANDARD_ATTRS
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str, fmt: str, env: str) -> None:
    handler = logging.StreamHandler()
    handler.set_name(HANDLER_NAME)
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(
        JsonFormatter(env)
        if fmt == "json"
        else logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s")
    )
    root = logging.getLogger()
    # Replace only our own handler (create_app may run repeatedly, e.g. in tests) and
    # leave others, such as pytest's log capture, in place.
    root.handlers = [h for h in root.handlers if h.get_name() != HANDLER_NAME]
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Route uvicorn's own loggers through the root handler so everything shares one
    # format. Its per-request access log is replaced by our structured `http.access`
    # record (which carries the request id), so silence it.
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers = []
    access.propagate = False
