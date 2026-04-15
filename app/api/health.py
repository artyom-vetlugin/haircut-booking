from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db

router = APIRouter()


@router.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    """Liveness probe — confirms the process is running."""
    return {"status": "ok"}


@router.get("/health/db", tags=["ops"])
async def health_db(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    """Readiness probe — confirms the database is reachable."""
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "db": "connected"}
