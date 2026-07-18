"""
PSX Sentinel — Pipeline Orchestrator

Runs all data collectors in sequence for all monitored tickers.
Called by:
- Celery nightly task (run_nightly_pipeline)
- Manual trigger API endpoint (POST /api/v1/pipeline/trigger-sync)
- Standalone script (scripts/run_pipeline.py)

Execution sequence:
1. Seed companies (idempotent — skips existing)
2. Collect prices (PriceCollector via PSX DPS)
3. Collect announcements (AnnouncementCollector via PSX portal)
4. Parse PDFs (PDFParser — all announcement categories since Phase 5
   Session 7; text-layer PDFs land in raw_text, image-only PDFs leave
   it NULL. Note: runs BEFORE step 6's PSX Terminal mirror, so
   announcements mirrored tonight get their PDFs parsed on the NEXT
   run — a deliberate one-run lag kept to avoid reordering the
   long-verified stage sequence)
5. Collect news (NewsCollector via RSS feeds)
6. Collect fundamentals + announcement mirror (FundamentalsCollector
   via PSX Terminal)
7. Collect Dunya News business articles (DunyaNewsCollector, static
   HTML scrape)
8. Collect NCCPL FIPI/LIPI institutional flows (InstitutionalFlow-
   Collector via Playwright — see its docstring re: Cloudflare)

Each stage runs independently — a failure in one stage does NOT
prevent subsequent stages from executing. The pipeline always
returns a complete results dict even if some stages failed.
"""

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.announcement_collector import AnnouncementCollector
from app.collectors.dunya_news_collector import DunyaNewsCollector
from app.collectors.fundamentals_collector import FundamentalsCollector
from app.collectors.institutional_flow_collector import (
    InstitutionalFlowCollector,
)
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
            "fundamentals": {...},
            "dunya_news": {...},
            "institutional_flows": {...},
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
        logger.info(f"Step 1/8 - Seed complete: {seeded} companies inserted")
    except Exception as e:
        logger.error(f"Step 1/8 - Seed failed: {type(e).__name__}: {e}")
        results["seed"] = {"error": str(e)}

    # ── Step 2: Collect prices ────────────────────────────────────────────
    try:
        price_result = await PriceCollector(db).run_safe(tickers)
        results["prices"] = price_result
        logger.info(f"Step 2/8 - Prices complete: {price_result}")
    except Exception as e:
        logger.error(f"Step 2/8 - Price collection failed: {e}")
        results["prices"] = {"error": str(e)}

    # ── Step 3: Collect announcements ─────────────────────────────────────
    try:
        ann_result = await AnnouncementCollector(db).run_safe(tickers)
        results["announcements"] = ann_result
        logger.info(f"Step 3/8 - Announcements complete: {ann_result}")
    except Exception as e:
        logger.error(f"Step 3/8 - Announcement collection failed: {e}")
        results["announcements"] = {"error": str(e)}

    # ── Step 4: Parse quarterly result PDFs ───────────────────────────────
    try:
        pdf_result = await PDFParser(db).run_safe(tickers)
        results["pdfs"] = pdf_result
        logger.info(f"Step 4/8 - PDF parsing complete: {pdf_result}")
    except Exception as e:
        logger.error(f"Step 4/8 - PDF parsing failed: {e}")
        results["pdfs"] = {"error": str(e)}

    # ── Step 5: Collect news ──────────────────────────────────────────────
    try:
        news_result = await NewsCollector(db).run_safe(tickers)
        results["news"] = news_result
        logger.info(f"Step 5/8 - News complete: {news_result}")
    except Exception as e:
        logger.error(f"Step 5/8 - News collection failed: {e}")
        results["news"] = {"error": str(e)}

    # ── Step 6: Collect fundamentals + PSX Terminal announcement mirror ──
    try:
        fundamentals_result = await FundamentalsCollector(db).run_safe(tickers)
        results["fundamentals"] = fundamentals_result
        logger.info(f"Step 6/8 - Fundamentals complete: {fundamentals_result}")
    except Exception as e:
        logger.error(f"Step 6/8 - Fundamentals collection failed: {e}")
        results["fundamentals"] = {"error": str(e)}

    # ── Step 7: Collect Dunya News business articles ──────────────────────
    try:
        dunya_result = await DunyaNewsCollector(db).run_safe(tickers)
        results["dunya_news"] = dunya_result
        logger.info(f"Step 7/8 - Dunya News complete: {dunya_result}")
    except Exception as e:
        logger.error(f"Step 7/8 - Dunya News collection failed: {e}")
        results["dunya_news"] = {"error": str(e)}

    # ── Step 8: Collect NCCPL FIPI/LIPI institutional flows ───────────────
    # Playwright-driven; a no-op (0 rows, logged) on any host where NCCPL's
    # Cloudflare challenge blocks automation — never aborts the pipeline.
    try:
        flow_result = await InstitutionalFlowCollector(db).run_safe(tickers)
        results["institutional_flows"] = flow_result
        logger.info(f"Step 8/8 - Institutional flows complete: {flow_result}")
    except Exception as e:
        logger.error(f"Step 8/8 - Institutional flow collection failed: {e}")
        results["institutional_flows"] = {"error": str(e)}

    logger.info("Full pipeline complete")
    logger.info(f"Pipeline results: {results}")
    return results
