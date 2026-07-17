"""
PSX Sentinel — Standalone Pipeline Runner

Run the data collection pipeline from the command line.
Useful for manual testing, initial data seeding, and debugging.

Usage from the backend/ directory:
    python scripts/run_pipeline.py
    python scripts/run_pipeline.py --tickers ENGRO,LUCK
    python scripts/run_pipeline.py --seed-only
    python scripts/run_pipeline.py --prices-only
    python scripts/run_pipeline.py --news-only
    python scripts/run_pipeline.py --fundamentals-only
    python scripts/run_pipeline.py --dunya-only
    python scripts/run_pipeline.py --flows-only
"""

import argparse
import asyncio
import os
import sys

# Add backend/ to Python path so imports work correctly
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


async def main(
    tickers: list[str],
    seed_only: bool = False,
    prices_only: bool = False,
    news_only: bool = False,
    fundamentals_only: bool = False,
    dunya_only: bool = False,
    flows_only: bool = False,
) -> None:
    """
    Main async entry point for the pipeline runner.

    Initializes the database, then runs the requested pipeline
    stage(s) within an async database session.
    """
    from loguru import logger

    from app.db.session import AsyncSessionLocal, init_db

    logger.info("Initializing database...")
    await init_db()

    async with AsyncSessionLocal() as db:
        # ── Seed only ─────────────────────────────────────────────────
        if seed_only:
            from app.collectors.seed_data import seed_companies

            count = await seed_companies(db)
            logger.info(f"Seeded {count} companies")
            return

        # ── Prices only ───────────────────────────────────────────────
        if prices_only:
            from app.collectors.seed_data import seed_companies

            await seed_companies(db)  # Ensure companies exist

            from app.collectors.price_collector import PriceCollector

            result = await PriceCollector(db).run_safe(tickers)
            logger.info(f"Price collection result: {result}")
            return

        # ── News only ─────────────────────────────────────────────────
        if news_only:
            from app.collectors.seed_data import seed_companies

            await seed_companies(db)  # Ensure companies exist

            from app.collectors.news_collector import NewsCollector

            result = await NewsCollector(db).run_safe(tickers)
            logger.info(f"News collection result: {result}")
            return

        # ── Institutional flows only (NCCPL FIPI/LIPI) ────────────────
        if flows_only:
            from app.collectors.institutional_flow_collector import (
                InstitutionalFlowCollector,
            )

            result = await InstitutionalFlowCollector(db).run_safe(tickers)
            logger.info(f"Institutional flow collection result: {result}")
            return

        # ── Dunya News only ───────────────────────────────────────────
        if dunya_only:
            from app.collectors.seed_data import seed_companies

            await seed_companies(db)  # Ensure companies exist

            from app.collectors.dunya_news_collector import (
                DunyaNewsCollector,
            )

            result = await DunyaNewsCollector(db).run_safe(tickers)
            logger.info(f"Dunya News collection result: {result}")
            return

        # ── Fundamentals only ─────────────────────────────────────────
        if fundamentals_only:
            from app.collectors.seed_data import seed_companies

            await seed_companies(db)  # Ensure companies exist

            from app.collectors.fundamentals_collector import (
                FundamentalsCollector,
            )

            result = await FundamentalsCollector(db).run_safe(tickers)
            logger.info(f"Fundamentals collection result: {result}")
            return

        # ── Full pipeline ─────────────────────────────────────────────
        from app.collectors.pipeline import run_full_pipeline

        results = await run_full_pipeline(db, tickers)
        logger.info("=" * 60)
        logger.info("PIPELINE RESULTS:")
        logger.info("=" * 60)
        for stage, result in results.items():
            logger.info(f"  {stage}: {result}")
        logger.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="PSX Sentinel Data Pipeline Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/run_pipeline.py                    # Full pipeline, all tickers
  python scripts/run_pipeline.py --seed-only        # Only seed company data
  python scripts/run_pipeline.py --tickers ENGRO,LUCK  # Full pipeline, 2 tickers
  python scripts/run_pipeline.py --prices-only      # Only collect prices
  python scripts/run_pipeline.py --news-only        # Only collect news
        """,
    )
    parser.add_argument(
        "--tickers",
        type=str,
        default=None,
        help="Comma-separated tickers (default: all 10 from settings)",
    )
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="Only seed company data, skip all collectors",
    )
    parser.add_argument(
        "--prices-only",
        action="store_true",
        help="Only collect price data (seeds companies first)",
    )
    parser.add_argument(
        "--news-only",
        action="store_true",
        help="Only collect news data (seeds companies first)",
    )
    parser.add_argument(
        "--fundamentals-only",
        action="store_true",
        help=(
            "Only collect fundamentals + PSX Terminal announcements "
            "(seeds companies first)"
        ),
    )
    parser.add_argument(
        "--dunya-only",
        action="store_true",
        help="Only collect Dunya News articles (seeds companies first)",
    )
    parser.add_argument(
        "--flows-only",
        action="store_true",
        help=(
            "Only collect NCCPL FIPI/LIPI institutional flows via "
            "Playwright (may be blocked by Cloudflare — see collector "
            "docstring)"
        ),
    )

    args = parser.parse_args()

    # Resolve tickers
    tickers = None
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",")]

    if tickers is None:
        from app.core.config import get_settings

        tickers = get_settings().tickers_list

    print(f"PSX Sentinel Pipeline Runner")
    print(f"Tickers: {', '.join(tickers)}")
    _mode = (
        "seed-only" if args.seed_only
        else "prices-only" if args.prices_only
        else "news-only" if args.news_only
        else "fundamentals-only" if args.fundamentals_only
        else "dunya-only" if args.dunya_only
        else "flows-only" if args.flows_only
        else "full pipeline"
    )
    print(f"Mode: {_mode}")
    print()

    asyncio.run(
        main(
            tickers,
            seed_only=args.seed_only,
            prices_only=args.prices_only,
            news_only=args.news_only,
            fundamentals_only=args.fundamentals_only,
            dunya_only=args.dunya_only,
            flows_only=args.flows_only,
        )
    )
