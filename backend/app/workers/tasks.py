"""
PSX Sentinel — Celery Task Definitions

Contains the two primary background tasks:
1. run_analysis — runs the data collection pipeline for a single ticker
2. run_nightly_pipeline — runs the full pipeline for all configured tickers

Both tasks are sync Celery functions that create their own async event
loops internally. This is the standard pattern for running async code
inside Celery workers (which are synchronous by default).

The run_analysis task has retry logic (max 2 retries, 30s delay) and
time limits (300s soft, 360s hard) to prevent runaway tasks.
"""

import asyncio

from loguru import logger

from app.workers.celery_app import celery_app


@celery_app.task(
    bind=True,
    name="app.workers.tasks.run_analysis",
    max_retries=2,
    default_retry_delay=30,
    soft_time_limit=300,
    time_limit=360,
)
def run_analysis(self, ticker: str) -> dict:
    """
    Run the data collection pipeline for a single ticker.

    This is a sync Celery task that creates its own async event loop
    to run the async pipeline.

    Steps:
    1. Log task start with task ID
    2. Create a new async event loop (Celery workers don't have one)
    3. Run the async pipeline inside the loop
    4. On failure: retry up to max_retries times with 30s delay
    5. Clean up the Redis analysis lock on completion
    6. Return result dict with ticker and status
    """
    logger.info(
        f"Starting analysis task for {ticker} | "
        f"task_id={self.request.id}"
    )

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                _run_analysis_async(ticker)
            )
        finally:
            loop.close()

        logger.info(f"Analysis complete for {ticker}")
        return {
            "ticker": ticker,
            "status": "complete",
            "result": result,
        }

    except Exception as exc:
        logger.error(
            f"Analysis failed for {ticker}: {type(exc).__name__}: {exc}"
        )
        # Clean up the Redis lock so the ticker can be re-queued
        _cleanup_lock(ticker)
        raise self.retry(exc=exc)


async def _run_analysis_async(ticker: str) -> dict:
    """
    Async implementation of the analysis pipeline.

    Creates its own DB session isolated from the FastAPI request
    lifecycle. Runs the full data pipeline for a single ticker,
    then cleans up the Redis analysis lock.
    """
    from app.collectors.pipeline import run_full_pipeline
    from app.core.redis_client import redis_client
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        try:
            result = await run_full_pipeline(db, tickers=[ticker])
            return result
        finally:
            # Clean up the analysis lock
            await redis_client.delete_cached(
                f"analysis_running:{ticker}"
            )


def _cleanup_lock(ticker: str) -> None:
    """
    Synchronous cleanup of the Redis analysis lock.
    Called on task failure before retry to allow re-queuing.
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            from app.core.redis_client import redis_client

            loop.run_until_complete(
                redis_client.delete_cached(f"analysis_running:{ticker}")
            )
        finally:
            loop.close()
    except Exception as e:
        logger.warning(
            f"Could not clean up analysis lock for {ticker}: {e}"
        )


@celery_app.task(
    name="app.workers.tasks.run_nightly_pipeline",
    soft_time_limit=3600,
    time_limit=3900,
)
def run_nightly_pipeline() -> dict:
    """
    Nightly pipeline: run the full data collection pipeline for all tickers.

    Runs at NIGHTLY_PIPELINE_HOUR (default 8 PM PKT) via Celery Beat.
    Unlike Phase 1B which fanned out per-ticker tasks, this now runs
    the full pipeline in a single pass — the pipeline orchestrator
    handles all tickers sequentially within one task.

    Steps:
    1. Read ticker list from settings
    2. Create a new async event loop
    3. Run the full pipeline with all tickers
    4. Return combined results dict
    """
    from app.core.config import get_settings

    settings = get_settings()
    tickers = settings.tickers_list
    logger.info(
        f"Nightly pipeline started -- "
        f"{len(tickers)} tickers: {', '.join(tickers)}"
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        from app.collectors.pipeline import run_full_pipeline
        from app.db.session import AsyncSessionLocal

        async def _run():
            async with AsyncSessionLocal() as db:
                return await run_full_pipeline(db, tickers)

        result = loop.run_until_complete(_run())
        logger.info(f"Nightly pipeline complete: {result}")
        return result
    except Exception as e:
        logger.error(
            f"Nightly pipeline failed: {type(e).__name__}: {e}"
        )
        return {"status": "failed", "error": str(e)}
    finally:
        loop.close()
