import logging
from collections import deque
from typing import Annotated, Optional

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.core.config import settings
from app.integrations.telegram.client import bot_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhook"])

# In-memory dedup: track the last _DEDUP_MAX update_ids to absorb Telegram retries.
# Telegram may resend an update if it does not receive a timely 200 response.
_DEDUP_MAX = 2_000
_seen_ids: set[int] = set()
_seen_queue: deque[int] = deque()


def _is_duplicate(update_id: int) -> bool:
    """Return True if this update_id was already processed; register it otherwise."""
    if update_id in _seen_ids:
        return True
    _seen_ids.add(update_id)
    _seen_queue.append(update_id)
    if len(_seen_queue) > _DEDUP_MAX:
        _seen_ids.discard(_seen_queue.popleft())
    return False


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
    update_id: int | None = data.get("update_id")

    if update_id is not None and _is_duplicate(update_id):
        logger.info("Skipping duplicate Telegram update update_id=%s", update_id)
        return {"ok": True}

    try:
        await bot_client.process_update(data)
    except Exception:
        logger.exception("Failed to process Telegram update update_id=%s", update_id)
        # Always return 200 to Telegram to prevent update retries.

    return {"ok": True}
