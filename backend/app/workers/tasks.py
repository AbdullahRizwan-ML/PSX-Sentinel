"""
PSX Sentinel — Celery Task Definitions

Contains the two primary background tasks:
1. run_analysis — runs the 4-agent analysis pipeline for a single ticker
2. run_nightly_pipeline — queues analysis for all configured tickers

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
    Run the 4-agent analysis pipeline for a single ticker.

    This is a sync Celery task that creates its own async event loop
    to run the async analysis pipeline.

    Steps:
    1. Log task start with task ID
    2. Create a new async event loop (Celery workers don't have one)
    3. Run the async analysis pipeline inside the loop
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

    Creates its own DB session and Redis client for this task's
    lifetime, isolated from the FastAPI request lifecycle.

    In Phase 2, this will import and run the AnalysisOrchestrator.
    Currently returns a confirmation dict to verify the pipeline runs.
    """
    from app.core.redis_client import RedisClient
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        redis = RedisClient()
        try:
            # Phase 2: Replace with real orchestrator call:
            # from app.agents.orchestrator import AnalysisOrchestrator
            # orchestrator = AnalysisOrchestrator(db, redis)
            # report = await orchestrator.analyze(ticker)
            # return {"report_id": str(report.id), "ticker": ticker}

            logger.info(
                f"Analysis pipeline executing for {ticker} — "
                f"orchestrator will be connected in Phase 2"
            )
            return {"ticker": ticker, "status": "pipeline_ready"}

        finally:
            await redis.close()
            # Clean up the analysis lock
            from app.core.redis_client import redis_client

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
    Nightly pipeline: queue analysis tasks for all configured tickers.

    Runs at NIGHTLY_PIPELINE_HOUR (default 8 PM PKT) via Celery Beat.
    Each ticker gets its own run_analysis task queued to the analysis
    queue, enabling parallel processing across workers.

    Steps:
    1. Read ticker list from settings
    2. Queue an individual run_analysis task for each ticker
    3. Log the queued tickers
    4. Return summary dict with count and ticker list
    """
    from app.core.config import get_settings

    settings = get_settings()
    tickers = settings.tickers_list
    logger.info(
        f"Nightly pipeline started — "
        f"queuing {len(tickers)} tickers for analysis"
    )

    queued_tasks = []
    for ticker in tickers:
        task = run_analysis.apply_async(
            args=[ticker],
            queue="analysis",
        )
        queued_tasks.append(ticker)
        logger.info(
            f"Queued analysis for {ticker} — task_id={task.id}"
        )

    logger.info(
        f"Nightly pipeline complete — "
        f"{len(queued_tasks)} tickers queued"
    )

    return {
        "status": "queued",
        "tickers_queued": len(queued_tasks),
        "tickers": queued_tasks,
    }
