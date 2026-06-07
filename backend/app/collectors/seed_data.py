"""
PSX Sentinel — Company Seed Data

Seeds the Company table with all 10 monitored PSX tickers.
This must run before any collector can insert price/news data
because all data tables have foreign keys pointing to companies.ticker.

The seed function is idempotent — it skips tickers that already exist,
making it safe to run on every pipeline execution.

FUTURE ENRICHMENT SOURCE (Phase 2B):
    Sarmaaya.pk (https://sarmaaya.pk/indexes/KSE100) is a PSX-authorized
    data redistributor that provides live KSE-100 data including market
    cap, P/E ratio, and 52-week high/low for all listed companies. The
    page is JavaScript-rendered, so scraping requires Playwright. Once
    available, use it to keep market_cap_pkr and other fundamentals
    updated automatically instead of using static seed values.
"""

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Company


COMPANY_SEED_DATA = [
    {
        "ticker": "ENGRO",
        "name": "Engro Corporation Limited",
        "sector": "Chemicals",
        "market_cap_pkr": 280_000_000_000.0,
        "is_kse30": True,
        "is_kmi30": True,
    },
    {
        "ticker": "LUCK",
        "name": "Lucky Cement Limited",
        "sector": "Cement",
        "market_cap_pkr": 350_000_000_000.0,
        "is_kse30": True,
        "is_kmi30": True,
    },
    {
        "ticker": "OGDC",
        "name": "Oil & Gas Development Company Limited",
        "sector": "Oil & Gas",
        "market_cap_pkr": 750_000_000_000.0,
        "is_kse30": True,
        "is_kmi30": False,
    },
    {
        "ticker": "PPL",
        "name": "Pakistan Petroleum Limited",
        "sector": "Oil & Gas",
        "market_cap_pkr": 250_000_000_000.0,
        "is_kse30": True,
        "is_kmi30": False,
    },
    {
        "ticker": "MCB",
        "name": "MCB Bank Limited",
        "sector": "Banking",
        "market_cap_pkr": 330_000_000_000.0,
        "is_kse30": True,
        "is_kmi30": True,
    },
    {
        "ticker": "HBL",
        "name": "Habib Bank Limited",
        "sector": "Banking",
        "market_cap_pkr": 290_000_000_000.0,
        "is_kse30": True,
        "is_kmi30": False,
    },
    {
        "ticker": "UBL",
        "name": "United Bank Limited",
        "sector": "Banking",
        "market_cap_pkr": 260_000_000_000.0,
        "is_kse30": True,
        "is_kmi30": False,
    },
    {
        "ticker": "MARI",
        "name": "Mari Petroleum Company Limited",
        "sector": "Oil & Gas",
        "market_cap_pkr": 420_000_000_000.0,
        "is_kse30": True,
        "is_kmi30": False,
    },
    {
        "ticker": "PSO",
        "name": "Pakistan State Oil Company Limited",
        "sector": "Oil & Gas",
        "market_cap_pkr": 115_000_000_000.0,
        "is_kse30": True,
        "is_kmi30": False,
    },
    {
        "ticker": "MEBL",
        "name": "Meezan Bank Limited",
        "sector": "Banking",
        "market_cap_pkr": 310_000_000_000.0,
        "is_kse30": True,
        "is_kmi30": True,
    },
]


async def seed_companies(db: AsyncSession) -> int:
    """
    Insert company records. Skip if ticker already exists.

    Uses an explicit existence check per ticker rather than
    INSERT ... ON CONFLICT because the Company model uses
    ticker as a natural string primary key (not auto-generated).

    Returns the count of newly inserted records.
    """
    inserted = 0

    for data in COMPANY_SEED_DATA:
        result = await db.execute(
            select(Company).where(Company.ticker == data["ticker"])
        )
        existing = result.scalar_one_or_none()

        if existing is None:
            company = Company(**data)
            db.add(company)
            inserted += 1
            logger.info(
                f"Seeded company: {data['ticker']} -- {data['name']}"
            )
        else:
            logger.debug(
                f"Company {data['ticker']} already exists, skipping"
            )

    await db.commit()
    logger.info(
        f"Company seeding complete: {inserted} inserted, "
        f"{len(COMPANY_SEED_DATA) - inserted} already existed"
    )
    return inserted
