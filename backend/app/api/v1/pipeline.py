"""
PSX Sentinel — Pipeline Management API Routes

Provides endpoints for monitoring and triggering data pipelines.

GET  /pipeline/status       — View last 5 pipeline runs (no auth)
POST /pipeline/trigger      — Queue nightly pipeline via Celery (auth)
POST /pipeline/seed         — Seed company data directly (auth)
POST /pipeline/trigger-sync — Run full pipeline synchronously (auth, dev only)
"""

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_active_user
from app.db.models import PipelineRun, User
from app.db.session import get_db

router = APIRouter(tags=["Pipeline"])


@router.get(
    "/status",
    summary="Get the status of recent pipeline runs",
)
async def get_pipeline_status(
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    """
    Query the last 5 PipelineRun records ordered by started_at DESC.

    No authentication required — this is used by monitoring dashboards
    and health check systems.

    Returns a list of dicts with pipeline metadata.
    """
    try:
        result = await db.execute(
            select(PipelineRun)
            .order_by(PipelineRun.started_at.desc())
            .limit(5)
        )
        runs = result.scalars().all()

        return [
            {
                "id": str(run.id),
                "pipeline_name": run.pipeline_name,
                "status": run.status,
                "started_at": (
                    run.started_at.isoformat()
                    if run.started_at
                    else None
                ),
                "completed_at": (
                    run.completed_at.isoformat()
                    if run.completed_at
                    else None
                ),
                "tickers_processed": run.tickers_processed,
                "tickers_failed": run.tickers_failed,
                "error_log": run.error_log,
            }
            for run in runs
        ]

    except Exception as e:
        logger.error(f"Error fetching pipeline status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch pipeline status",
        )


@router.post(
    "/trigger",
    summary="Queue the nightly pipeline via Celery",
)
async def trigger_pipeline(
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Queue the nightly pipeline as a Celery background task.

    Checks if a pipeline is already RUNNING — returns 409 if so
    to prevent concurrent pipeline executions.
    """
    # Check if a pipeline is already running
    try:
        result = await db.execute(
            select(PipelineRun)
            .where(PipelineRun.status == "RUNNING")
            .limit(1)
        )
        running = result.scalar_one_or_none()
        if running:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Pipeline '{running.pipeline_name}' is already running "
                    f"(started at {running.started_at.isoformat()}). "
                    f"Wait for it to complete."
                ),
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking pipeline status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check pipeline status",
        )

    # Queue via Celery
    from app.workers.tasks import run_nightly_pipeline

    task = run_nightly_pipeline.delay()
    logger.info(
        f"Pipeline triggered by user={user.email}, "
        f"task_id={task.id}"
    )

    return {
        "message": "Pipeline triggered",
        "status": "queued",
        "task_id": str(task.id),
    }


@router.post(
    "/seed",
    summary="Seed company data into the database",
)
async def seed_companies_endpoint(
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Run seed_companies() directly (not via Celery — it's fast).

    Inserts the 10 monitored PSX companies into the Company table.
    Idempotent — skips tickers that already exist.
    """
    from app.collectors.seed_data import seed_companies

    try:
        count = await seed_companies(db)
        logger.info(
            f"Seed endpoint called by user={user.email}: "
            f"{count} companies inserted"
        )
        return {
            "message": "Seed complete",
            "inserted": count,
        }
    except Exception as e:
        logger.error(f"Seed failed: {type(e).__name__}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Seed failed: {e}",
        )


@router.post(
    "/trigger-sync",
    summary="Run the full pipeline synchronously (dev/testing only)",
)
async def trigger_pipeline_sync(
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Run the full data pipeline directly in the request (blocking).

    FOR DEVELOPMENT/TESTING ONLY — in production, use /trigger
    to queue via Celery.

    This is useful for:
    - Initial data seeding
    - Testing collectors without Celery infrastructure
    - Debugging pipeline issues

    Warning: This request may take 5-10 minutes to complete
    depending on the number of tickers and data sources.
    """
    from app.collectors.pipeline import run_full_pipeline

    logger.info(
        f"Sync pipeline trigger by user={user.email} "
        f"-- this may take several minutes"
    )

    try:
        results = await run_full_pipeline(db)
        return {
            "message": "Pipeline complete",
            "results": results,
        }
    except Exception as e:
        logger.error(
            f"Sync pipeline failed: {type(e).__name__}: {e}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Pipeline failed: {e}",
        )
