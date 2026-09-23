import logging

from fastapi import FastAPI

from app.api import health
from app.api.v1 import api_router
from app.core.config import get_settings
from app.errors import register_exception_handlers


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app = FastAPI(title="Expert Listing API", version="0.1.0")
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(api_router)
    return app


app = create_app()
