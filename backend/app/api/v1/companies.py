"""
PSX Sentinel — Companies & Market Data API Routes

Provides endpoints for browsing companies, viewing price history,
announcements, news, intelligence reports, and triggering analysis.
Market summary and movers endpoints aggregate across all companies.

All responses are cached in Redis with appropriate TTLs to reduce
database load during high-traffic periods.
"""

import json
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.redis_client import (
    COMPANY_CACHE_KEY,
    REPORT_CACHE_KEY,
    redis_client,
)
from app.core.security import get_current_active_user
from app.db.models import (
    Announcement,
    Company,
    DailyPrice,
    IntelligenceReport,
    NewsArticle,
    User,
)
from app.db.session import get_db
from app.schemas.company import (
    AnnouncementResponse,
    CompanyDetailResponse,
    CompanyResponse,
    MarketSummaryResponse,
    NewsArticleResponse,
    PaginatedResponse,
    PricePoint,
)
from app.schemas.intelligence import IntelligenceReportResponse

settings = get_settings()

router = APIRouter(tags=["Companies", "Market"])


# ═══════════════════════════════════════════════════════════════════════════════
# Company Listing & Detail
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/companies",
    response_model=PaginatedResponse,
    summary="List all PSX companies with pagination and filtering",
)
async def list_companies(
    sector: str | None = Query(default=None, description="Filter by sector"),
    search: str | None = Query(
        default=None, description="Search ticker or company name"
    ),
    page: int = Query(default=1, ge=1, description="Page number"),
    limit: int = Query(default=20, ge=1, le=100, description="Items per page"),
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse:
    """
    Query companies with optional sector filter and text search.
    Results are cached in Redis for 60 seconds per unique query key.
    """
    cache_key = f"companies:list:{sector}:{search}:{page}:{limit}"
    cached = await redis_client.get_cached(cache_key)
    if cached:
        return PaginatedResponse.model_validate_json(cached)

    try:
        query = select(Company)
        count_query = select(func.count()).select_from(Company)

        if sector:
            query = query.where(func.lower(Company.sector) == sector.lower())
            count_query = count_query.where(
                func.lower(Company.sector) == sector.lower()
            )

        if search:
            search_pattern = f"%{search.upper()}%"
            query = query.where(
                (Company.ticker.ilike(search_pattern))
                | (Company.name.ilike(f"%{search}%"))
            )
            count_query = count_query.where(
                (Company.ticker.ilike(search_pattern))
                | (Company.name.ilike(f"%{search}%"))
            )

        total_result = await db.execute(count_query)
        total = total_result.scalar() or 0

        offset = (page - 1) * limit
        query = query.order_by(Company.ticker).offset(offset).limit(limit)
        result = await db.execute(query)
        companies = result.scalars().all()

        items = [
            CompanyResponse.model_validate(c).model_dump(mode="json")
            for c in companies
        ]
        response = PaginatedResponse.create(
            items=items, total=total, page=page, limit=limit
        )

        await redis_client.set_cached(
            cache_key, response.model_dump_json(), ttl_seconds=60
        )
        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing companies: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch companies",
        )


@router.get(
    "/companies/{ticker}",
    response_model=CompanyDetailResponse,
    summary="Get detailed company information",
)
async def get_company_detail(
    ticker: str,
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> CompanyDetailResponse:
    """
    Fetch company detail with latest price and conviction score.
    Cached in Redis for 3600 seconds (1 hour).
    """
    cache_key = COMPANY_CACHE_KEY.format(ticker=ticker.upper())
    cached = await redis_client.get_cached(cache_key)
    if cached:
        return CompanyDetailResponse.model_validate_json(cached)

    try:
        result = await db.execute(
            select(Company).where(Company.ticker == ticker.upper())
        )
        company = result.scalar_one_or_none()
    except Exception as e:
        logger.error(f"Database error fetching company {ticker}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        )

    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company '{ticker}' not found",
        )

    # Fetch latest price
    latest_price = None
    latest_change_pct = None
    try:
        price_result = await db.execute(
            select(DailyPrice)
            .where(DailyPrice.ticker == ticker.upper())
            .order_by(DailyPrice.date.desc())
            .limit(1)
        )
        price = price_result.scalar_one_or_none()
        if price:
            latest_price = price.close
            latest_change_pct = price.change_pct
    except Exception as e:
        logger.warning(f"Could not fetch latest price for {ticker}: {e}")

    # Fetch latest conviction score
    latest_conviction = None
    try:
        report_result = await db.execute(
            select(IntelligenceReport.conviction_score)
            .where(IntelligenceReport.ticker == ticker.upper())
            .order_by(IntelligenceReport.generated_at.desc())
            .limit(1)
        )
        conviction_row = report_result.scalar_one_or_none()
        if conviction_row is not None:
            latest_conviction = conviction_row
    except Exception as e:
        logger.warning(f"Could not fetch conviction for {ticker}: {e}")

    response = CompanyDetailResponse(
        ticker=company.ticker,
        name=company.name,
        sector=company.sector,
        market_cap_pkr=company.market_cap_pkr,
        is_kse30=company.is_kse30,
        is_kmi30=company.is_kmi30,
        last_updated=company.last_updated,
        shares_outstanding=company.shares_outstanding,
        listing_date=company.listing_date,
        latest_price=latest_price,
        latest_change_pct=latest_change_pct,
        latest_conviction_score=latest_conviction,
    )

    await redis_client.set_cached(
        cache_key, response.model_dump_json(), ttl_seconds=3600
    )
    return response


# ═══════════════════════════════════════════════════════════════════════════════
# Price, Announcement, News Data
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/companies/{ticker}/prices",
    response_model=list[PricePoint],
    summary="Get historical price data for a company",
)
async def get_prices(
    ticker: str,
    start_date: date | None = Query(
        default=None, description="Start date (YYYY-MM-DD)"
    ),
    end_date: date | None = Query(
        default=None, description="End date (YYYY-MM-DD)"
    ),
    limit: int = Query(default=90, ge=1, le=365, description="Max records"),
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> list[PricePoint]:
    """
    Fetch OHLCV price data for a ticker.
    Defaults to last 90 days if no date range is provided.
    """
    try:
        # Verify ticker exists
        company_check = await db.execute(
            select(Company.ticker).where(Company.ticker == ticker.upper())
        )
        if not company_check.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Company '{ticker}' not found",
            )

        query = select(DailyPrice).where(
            DailyPrice.ticker == ticker.upper()
        )

        if start_date:
            query = query.where(DailyPrice.date >= start_date)
        else:
            default_start = date.today() - timedelta(days=90)
            query = query.where(DailyPrice.date >= default_start)

        if end_date:
            query = query.where(DailyPrice.date <= end_date)

        query = query.order_by(DailyPrice.date.desc()).limit(limit)
        result = await db.execute(query)
        prices = result.scalars().all()

        return [PricePoint.model_validate(p) for p in prices]

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching prices for {ticker}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch price data",
        )


@router.get(
    "/companies/{ticker}/announcements",
    response_model=PaginatedResponse,
    summary="Get corporate announcements for a company",
)
async def get_announcements(
    ticker: str,
    category: str | None = Query(
        default=None,
        description="Filter by category: QUARTERLY_RESULT, DIVIDEND, etc.",
    ),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse:
    """Fetch paginated announcements for a ticker, optionally filtered by category."""
    try:
        # Verify ticker exists
        company_check = await db.execute(
            select(Company.ticker).where(Company.ticker == ticker.upper())
        )
        if not company_check.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Company '{ticker}' not found",
            )

        query = select(Announcement).where(
            Announcement.ticker == ticker.upper()
        )
        count_query = select(func.count()).select_from(Announcement).where(
            Announcement.ticker == ticker.upper()
        )

        if category:
            query = query.where(Announcement.category == category.upper())
            count_query = count_query.where(
                Announcement.category == category.upper()
            )

        total_result = await db.execute(count_query)
        total = total_result.scalar() or 0

        offset = (page - 1) * limit
        query = (
            query.order_by(Announcement.announced_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await db.execute(query)
        announcements = result.scalars().all()

        items = [
            AnnouncementResponse.model_validate(a).model_dump(mode="json")
            for a in announcements
        ]
        return PaginatedResponse.create(
            items=items, total=total, page=page, limit=limit
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching announcements for {ticker}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch announcements",
        )


@router.get(
    "/companies/{ticker}/news",
    response_model=PaginatedResponse,
    summary="Get news articles for a company",
)
async def get_news(
    ticker: str,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse:
    """Fetch paginated news articles for a ticker, ordered by recency."""
    try:
        # Verify ticker exists
        company_check = await db.execute(
            select(Company.ticker).where(Company.ticker == ticker.upper())
        )
        if not company_check.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Company '{ticker}' not found",
            )

        count_query = select(func.count()).select_from(NewsArticle).where(
            NewsArticle.ticker == ticker.upper()
        )
        total_result = await db.execute(count_query)
        total = total_result.scalar() or 0

        offset = (page - 1) * limit
        query = (
            select(NewsArticle)
            .where(NewsArticle.ticker == ticker.upper())
            .order_by(NewsArticle.published_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await db.execute(query)
        articles = result.scalars().all()

        items = [
            NewsArticleResponse.model_validate(a).model_dump(mode="json")
            for a in articles
        ]
        return PaginatedResponse.create(
            items=items, total=total, page=page, limit=limit
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching news for {ticker}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch news articles",
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Intelligence Report & Analysis Trigger
# ═══════════════════════════════════════════════════════════════════════════════


@router.get(
    "/companies/{ticker}/report",
    response_model=IntelligenceReportResponse,
    summary="Get the latest intelligence report for a company",
)
async def get_latest_report(
    ticker: str,
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> IntelligenceReportResponse:
    """
    Fetch the most recent intelligence report for a ticker.
    Cached in Redis for 24 hours (86400 seconds).
    """
    today_str = date.today().isoformat()
    cache_key = REPORT_CACHE_KEY.format(ticker=ticker.upper(), date=today_str)
    cached = await redis_client.get_cached(cache_key)
    if cached:
        return IntelligenceReportResponse.model_validate_json(cached)

    try:
        result = await db.execute(
            select(IntelligenceReport)
            .where(IntelligenceReport.ticker == ticker.upper())
            .order_by(IntelligenceReport.generated_at.desc())
            .limit(1)
        )
        report = result.scalar_one_or_none()
    except Exception as e:
        logger.error(f"Error fetching report for {ticker}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch intelligence report",
        )

    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No intelligence report found for '{ticker}'",
        )

    response = IntelligenceReportResponse.model_validate(report)
    await redis_client.set_cached(
        cache_key, response.model_dump_json(), ttl_seconds=86400
    )
    return response


@router.post(
    "/companies/{ticker}/analyze",
    response_model=IntelligenceReportResponse,
    summary="Run the 4-agent analysis pipeline for a company",
)
async def trigger_analysis(
    ticker: str,
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> IntelligenceReportResponse:
    """
    Run the full 4-agent analysis pipeline synchronously and return
    the saved IntelligenceReport.

    - Validates that the ticker exists in the Company table
    - Checks if analysis is already running (429 if so)
    - Sets a Redis lock for 300 seconds to prevent duplicate runs
    - Runs TrendAnalyzer, NewsSynthesizer, FilingSceptic, Arbitrator
    - Returns the persisted IntelligenceReport
    """
    try:
        result = await db.execute(
            select(Company.ticker).where(Company.ticker == ticker.upper())
        )
        if not result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Company '{ticker}' not found",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Database error checking ticker {ticker}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error",
        )

    running_key = f"analysis_running:{ticker.upper()}"
    already_running = await redis_client.get_cached(running_key)
    if already_running:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Analysis already in progress for '{ticker}'. "
            f"Please wait for it to complete.",
        )

    await redis_client.set_cached(running_key, "1", ttl_seconds=300)

    try:
        from app.agents.orchestrator import AnalysisOrchestrator

        orchestrator = AnalysisOrchestrator(db)
        analysis = await orchestrator.analyze(ticker.upper())
        report = analysis["report"]

        logger.info(
            f"Analysis complete for {ticker.upper()} "
            f"by user={user.email}"
        )

        return IntelligenceReportResponse.model_validate(report)

    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except Exception as e:
        logger.error(
            f"Analysis failed for {ticker}: "
            f"{type(e).__name__}: {e}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Analysis failed: {type(e).__name__}",
        )
    finally:
        await redis_client.delete_cached(running_key)


# ═══════════════════════════════════════════════════════════════════════════════
# Market Summary & Movers
# ═══════════════════════════════════════════════════════════════════════════════


async def _get_market_data(
    db: AsyncSession, top_n: int
) -> MarketSummaryResponse:
    """
    Internal helper to fetch market gainers/losers.
    Queries the most recent trading day's prices and ranks by change_pct.
    """
    # Find the most recent trading date
    latest_date_result = await db.execute(
        select(func.max(DailyPrice.date))
    )
    latest_date = latest_date_result.scalar()

    if not latest_date:
        return MarketSummaryResponse(
            top_gainers=[],
            top_losers=[],
            total_companies=0,
            market_date=date.today(),
        )

    # Fetch all prices for the latest date with company names
    query = (
        select(DailyPrice, Company.name)
        .join(Company, DailyPrice.ticker == Company.ticker)
        .where(DailyPrice.date == latest_date)
        .where(DailyPrice.change_pct.is_not(None))
    )
    result = await db.execute(query)
    rows = result.all()

    # Build sorted lists
    price_data = [
        {
            "ticker": row[0].ticker,
            "name": row[1],
            "change_pct": row[0].change_pct,
            "close": row[0].close,
        }
        for row in rows
    ]

    sorted_by_change = sorted(
        price_data, key=lambda x: x["change_pct"] or 0, reverse=True
    )

    # Count total companies
    count_result = await db.execute(
        select(func.count()).select_from(Company)
    )
    total = count_result.scalar() or 0

    return MarketSummaryResponse(
        top_gainers=sorted_by_change[:top_n],
        top_losers=sorted_by_change[-top_n:][::-1]
        if len(sorted_by_change) >= top_n
        else sorted(
            price_data, key=lambda x: x["change_pct"] or 0
        )[:top_n],
        total_companies=total,
        market_date=latest_date,
    )


@router.get(
    "/market/summary",
    response_model=MarketSummaryResponse,
    summary="Get market summary with top 5 gainers and losers",
)
async def get_market_summary(
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> MarketSummaryResponse:
    """
    Market overview: top 5 gainers, top 5 losers, total companies.
    Cached for 300 seconds (5 minutes).
    """
    cache_key = "market:summary"
    cached = await redis_client.get_cached(cache_key)
    if cached:
        return MarketSummaryResponse.model_validate_json(cached)

    try:
        response = await _get_market_data(db, top_n=5)
        await redis_client.set_cached(
            cache_key, response.model_dump_json(), ttl_seconds=300
        )
        return response
    except Exception as e:
        logger.error(f"Error fetching market summary: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch market summary",
        )


@router.get(
    "/market/movers",
    response_model=MarketSummaryResponse,
    summary="Get top 3 market movers (gainers + losers)",
)
async def get_market_movers(
    user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> MarketSummaryResponse:
    """
    Compact market movers: top 3 gainers, top 3 losers.
    Cached for 300 seconds (5 minutes).
    """
    cache_key = "market:movers"
    cached = await redis_client.get_cached(cache_key)
    if cached:
        return MarketSummaryResponse.model_validate_json(cached)

    try:
        response = await _get_market_data(db, top_n=3)
        await redis_client.set_cached(
            cache_key, response.model_dump_json(), ttl_seconds=300
        )
        return response
    except Exception as e:
        logger.error(f"Error fetching market movers: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch market movers",
        )
