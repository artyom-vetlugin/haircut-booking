import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from app.api import api_router
from app.core.config import settings
from app.core.correlation import set_correlation_id
from app.core.logging import configure_logging
from app.integrations.telegram.client import bot_client
from app.use_cases.deps import get_mcp_client, initialize_calendar_adapter

configure_logging(debug=settings.debug)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    initialize_calendar_adapter()
    mcp_client = get_mcp_client()
    await bot_client.initialize()
    if mcp_client is not None:
        await mcp_client.start()
    if settings.telegram_webhook_url:
        await bot_client.register_webhook(
            settings.telegram_webhook_url,
            settings.telegram_webhook_secret,
        )
    yield
    if mcp_client is not None:
        await mcp_client.stop()
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

    @app.middleware("http")
    async def correlation_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        cid = request.headers.get("X-Correlation-Id") or str(uuid.uuid4())
        set_correlation_id(cid)
        response = await call_next(request)
        response.headers["X-Correlation-Id"] = cid
        return response

    app.include_router(api_router)
    return app


app = create_app()
