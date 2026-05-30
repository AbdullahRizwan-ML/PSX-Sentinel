"""
PSX Sentinel — Health Check API Route

Unauthenticated endpoint for infrastructure monitoring.
Performs real connectivity checks against PostgreSQL and Redis,
and reports the last pipeline run time for operational awareness.

Status logic:
- "healthy": DB connected AND Redis connected
- "degraded": DB connected but Redis disconnected
- "unhealthy": DB disconnected (regardless of Redis)
"""

from datetime import datetime

from fastapi import APIRouter, Depends
from loguru import logger
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis_client import redis_client
from app.db.models import PipelineRun
from app.db.session import get_db

router = APIRouter(tags=["System"])


@router.get(
    "/health",
    summary="System health check — no authentication required",
)
async def health_check(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Perform real connectivity checks against PostgreSQL and Redis.

    Returns:
        JSON with status, component health, and last pipeline run time.
        HTTP 200 is always returned — the 'status' field indicates health.
    """
    db_status = "disconnected"
    redis_status = "disconnected"
    last_pipeline_run = None

    # 1. PostgreSQL connectivity check
    try:
        await db.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception as e:
        logger.error(f"Health check — database unreachable: {e}")

    # 2. Redis connectivity check
    try:
        redis_ok = await redis_client.health_check()
        if redis_ok:
            redis_status = "connected"
    except Exception as e:
        logger.error(f"Health check — Redis unreachable: {e}")

    # 3. Last pipeline run time
    if db_status == "connected":
        try:
            result = await db.execute(
                select(PipelineRun.started_at)
                .order_by(PipelineRun.started_at.desc())
                .limit(1)
            )
            last_run = result.scalar_one_or_none()
            if last_run:
                last_pipeline_run = last_run.isoformat()
        except Exception as e:
            logger.warning(
                f"Health check — could not fetch last pipeline run: {e}"
            )

    # Determine overall status
    if db_status == "connected" and redis_status == "connected":
        overall_status = "healthy"
    elif db_status == "connected":
        overall_status = "degraded"
    else:
        overall_status = "unhealthy"

    return {
        "status": overall_status,
        "timestamp": datetime.utcnow().isoformat(),
        "database": db_status,
        "redis": redis_status,
        "last_pipeline_run": last_pipeline_run,
        "version": "1.0.0",
    }
