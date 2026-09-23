from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health
from app.api.v1 import api_router
from app.core.config import Settings, get_settings
from app.core.logging_config import configure_logging
from app.core.request_id import REQUEST_ID_HEADER, RequestContextMiddleware
from app.errors import register_exception_handlers


def add_middleware(app: FastAPI, settings: Settings) -> None:
    # Starlette runs the last-added middleware first. CORS is added first so the
    # request-context middleware wraps it: preflight responses get an X-Request-ID and
    # an access-log line too.
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", REQUEST_ID_HEADER],
            # Let browser clients read the new resource URL and the request id.
            expose_headers=["Location", REQUEST_ID_HEADER],
            allow_credentials=False,  # no cookies/auth: keeps "*" origins valid
            max_age=600,
        )
    app.add_middleware(RequestContextMiddleware)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format, settings.app_env)

    app = FastAPI(title="Expert Listing API", version="0.1.0")
    add_middleware(app, settings)
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(api_router)
    return app


app = create_app()
