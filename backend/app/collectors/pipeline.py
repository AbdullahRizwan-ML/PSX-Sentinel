"""
PSX Sentinel — Pipeline Orchestrator

Runs all data collectors in sequence for all monitored tickers.
Called by:
- Celery nightly task (run_nightly_pipeline)
- Manual trigger API endpoint (POST /api/v1/pipeline/trigger-sync)
- Standalone script (scripts/run_pipeline.py)

Execution sequence:
1. Seed companies (idempotent — skips existing)
2. Collect prices (PriceCollector via Yahoo Finance)
3. Collect announcements (AnnouncementCollector via PSX portal)
4. Parse PDFs (PDFParser for quarterly result documents)
5. Collect news (NewsCollector via RSS feeds)

Each stage runs independently — a failure in one stage does NOT
prevent subsequent stages from executing. The pipeline always
returns a complete results dict even if some stages failed.
"""

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.announcement_collector import AnnouncementCollector
from app.collectors.news_collector import NewsCollector
from app.collectors.pdf_parser import PDFParser
from app.collectors.price_collector import PriceCollector
from app.collectors.seed_data import seed_companies
from app.core.config import get_settings


async def run_full_pipeline(
    db: AsyncSession,
    tickers: list[str] | None = None,
) -> dict:
    """
    Run all collectors in sequence for the given tickers.

    Args:
        db: Async database session (caller must manage lifecycle)
        tickers: List of ticker symbols. Defaults to all configured
                 tickers from Settings.PSX_TICKERS if None.

    Returns:
        Combined results dict with one key per pipeline stage:
        {
            "seed": {"records_inserted": int} | {"error": str},
            "prices": {tickers_processed, tickers_failed, records_inserted},
            "announcements": {...},
            "pdfs": {...},
            "news": {...},
        }
    """
    settings = get_settings()

    if tickers is None:
        tickers = settings.tickers_list

    logger.info(
        f"Starting full pipeline for {len(tickers)} tickers: "
        f"{', '.join(tickers)}"
    )

    results: dict = {}

    # ── Step 1: Seed companies ────────────────────────────────────────────
    # Must run first because all data tables have FK to companies.ticker
    try:
        seeded = await seed_companies(db)
        results["seed"] = {"records_inserted": seeded}
        logger.info(f"Step 1/5 - Seed complete: {seeded} companies inserted")
    except Exception as e:
        logger.error(f"Step 1/5 - Seed failed: {type(e).__name__}: {e}")
        results["seed"] = {"error": str(e)}

    # ── Step 2: Collect prices ────────────────────────────────────────────
    try:
        price_result = await PriceCollector(db).run_safe(tickers)
        results["prices"] = price_result
        logger.info(f"Step 2/5 - Prices complete: {price_result}")
    except Exception as e:
        logger.error(f"Step 2/5 - Price collection failed: {e}")
        results["prices"] = {"error": str(e)}

    # ── Step 3: Collect announcements ─────────────────────────────────────
    try:
        ann_result = await AnnouncementCollector(db).run_safe(tickers)
        results["announcements"] = ann_result
        logger.info(f"Step 3/5 - Announcements complete: {ann_result}")
    except Exception as e:
        logger.error(f"Step 3/5 - Announcement collection failed: {e}")
        results["announcements"] = {"error": str(e)}

    # ── Step 4: Parse quarterly result PDFs ───────────────────────────────
    try:
        pdf_result = await PDFParser(db).run_safe(tickers)
        results["pdfs"] = pdf_result
        logger.info(f"Step 4/5 - PDF parsing complete: {pdf_result}")
    except Exception as e:
        logger.error(f"Step 4/5 - PDF parsing failed: {e}")
        results["pdfs"] = {"error": str(e)}

    # ── Step 5: Collect news ──────────────────────────────────────────────
    try:
        news_result = await NewsCollector(db).run_safe(tickers)
        results["news"] = news_result
        logger.info(f"Step 5/5 - News complete: {news_result}")
    except Exception as e:
        logger.error(f"Step 5/5 - News collection failed: {e}")
        results["news"] = {"error": str(e)}

    logger.info("Full pipeline complete")
    logger.info(f"Pipeline results: {results}")
    return results
