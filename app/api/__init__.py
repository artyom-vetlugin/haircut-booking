from fastapi import APIRouter

from app.api.health import router as health_router
from app.integrations.telegram.webhook import router as telegram_webhook_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(telegram_webhook_router)
