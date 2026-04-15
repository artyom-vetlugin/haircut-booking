import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.core.config import settings
from app.integrations.telegram.client import bot_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhook"])


@router.post("/telegram", status_code=status.HTTP_200_OK)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Annotated[Optional[str], Header()] = None,
) -> dict:  # type: ignore[type-arg]
    if settings.telegram_webhook_secret:
        if x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid secret token",
            )

    data = await request.json()
    try:
        await bot_client.process_update(data)
    except Exception:
        logger.exception("Failed to process Telegram update")
        # Always return 200 to Telegram to prevent update retries.

    return {"ok": True}
