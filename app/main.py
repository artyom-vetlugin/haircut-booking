from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import api_router
from app.core.config import settings
from app.core.logging import configure_logging
from app.integrations.telegram.client import bot_client

configure_logging(debug=settings.debug)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await bot_client.initialize()
    yield
    await bot_client.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Haircut Booking Bot",
        description="Telegram bot backend for haircut appointment scheduling.",
        version="0.1.0",
        docs_url="/docs" if settings.debug else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.include_router(api_router)
    return app


app = create_app()
