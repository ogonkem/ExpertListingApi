import logging

from fastapi import FastAPI

from app.api import health
from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.request_id import RequestIdLogFilter, RequestIdMiddleware
from app.errors import register_exception_handlers

_LOG_HANDLER_NAME = "expert-listing"


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.set_name(_LOG_HANDLER_NAME)
    handler.addFilter(RequestIdLogFilter())
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s")
    )
    root = logging.getLogger()
    # Replace only our own handler (create_app may run repeatedly, e.g. in tests) and
    # leave others, such as pytest's log capture, in place.
    root.handlers = [h for h in root.handlers if h.get_name() != _LOG_HANDLER_NAME]
    root.addHandler(handler)
    root.setLevel(level.upper())


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title="Expert Listing API", version="0.1.0")
    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(api_router)
    return app


app = create_app()
